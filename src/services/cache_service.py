"""
Redis Cache Service

Provides read-through caching for frequently accessed database entities:
- API keys
- Users
- Active sessions
- JWKS (for JWT validation)

Uses the same Redis instance as rate limiting for efficient resource usage.

Resilience: a circuit breaker guards every Redis access. During a timeout-mode
Redis outage the breaker opens after the first failure and all cache operations
degrade to a cache miss *immediately* (no per-request connect timeouts, no
global init lock contention). A single background task re-probes Redis with
exponential backoff and closes the breaker on recovery, so reconnection attempts
never run on the request path. Callers already treat a miss/None as "go to the
database", so this is correctness-safe.
"""

import json
import asyncio
from typing import Optional, Any, Dict, Callable, TypeVar
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from redis.asyncio import ConnectionPool
from redis.exceptions import RedisError

from src.core.config import settings
from src.core.circuit_breaker import CircuitBreaker, run_reprobe
from src.core.logging_config import get_core_logger

logger = get_core_logger()

T = TypeVar('T')


class CacheService:
    """
    Redis-based caching service with read-through pattern support.

    Features:
    - Automatic cache-aside (read-through) pattern
    - Configurable TTLs per entity type
    - Circuit-breaker graceful degradation on Redis failures
    - Shared connection pool with rate limiter
    - JSON serialization for complex objects
    """

    # Default TTLs for different entity types (in seconds)
    DEFAULT_TTLS = {
        "api_key": 300,      # 5 minutes - frequently used, rarely changes
        "user": 600,         # 10 minutes - changes infrequently
        "session": 300,      # 5 minutes - matches session check frequency
        "jwks": 3600,        # 1 hour - JWKS keys rarely change
    }

    def __init__(self):
        self._pool: Optional[ConnectionPool] = None
        self._redis: Optional[aioredis.Redis] = None
        self._initialized = False
        self._initialization_lock = asyncio.Lock()

        # Circuit breaker: open => skip Redis entirely and degrade to DB.
        self._breaker = CircuitBreaker("cache", initial_backoff=1.0, max_backoff=30.0)
        self._reprobe_task: Optional[asyncio.Task] = None

        # Track cache stats for monitoring
        self._stats = {
            "hits": 0,
            "misses": 0,
            "errors": 0,
            "sets": 0,
        }

    # ------------------------------------------------------------------ #
    # Connection lifecycle + circuit breaker
    # ------------------------------------------------------------------ #

    async def _connect_locked(self) -> bool:
        """Build (once) or reuse the pool and verify the connection. Assumes the
        init lock is held. Returns True on success. Does not touch the breaker."""
        try:
            if self._pool is None:
                # Pool is built once and reused — redis-py reconnects on demand,
                # so we must not rebuild it per attempt (that was pure churn).
                self._pool = ConnectionPool.from_url(
                    settings.REDIS_URL,
                    max_connections=settings.REDIS_MAX_CONNECTIONS,
                    socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
                    socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
                    decode_responses=True,
                    health_check_interval=30,  # recycle stale sockets after a failover
                    retry_on_timeout=True,
                )
            self._redis = aioredis.Redis(connection_pool=self._pool)
            await self._redis.ping()
            self._initialized = True
            return True
        except Exception as e:
            # Critical: drop the broken client so it is never yielded to callers
            # (otherwise the op pays a second connect timeout before degrading).
            self._redis = None
            self._initialized = False
            logger.error(
                "Failed to connect Redis cache service",
                error=str(e),
                event_type="cache_service_init_error",
            )
            return False

    async def _attempt_locked(self) -> bool:
        """Connect (if needed) and update the breaker, under the init lock."""
        if self._initialized and self._redis is not None:
            return True
        ok = await self._connect_locked()
        if ok:
            self._breaker.record_success()
        else:
            self._breaker.record_failure()
        return ok

    async def initialize(self) -> bool:
        """
        Initialize the Redis connection pool. Safe to call once at startup; also
        used as the single-flight (re)connect on the request path.

        Returns True if a usable connection is established.
        """
        if not settings.CACHE_ENABLED:
            logger.info(
                "Redis caching is disabled (CACHE_ENABLED=false)",
                event_type="cache_disabled",
            )
            return False

        async with self._initialization_lock:
            if self._initialized:
                return True
            # Breaker open: a recent attempt already failed — don't dial again here.
            if self._breaker.is_open():
                return False
            ok = await self._attempt_locked()

        if ok:
            logger.info(
                "Redis cache service initialized",
                redis_url=settings.REDIS_URL.split("@")[-1],  # Hide password
                event_type="cache_service_init",
            )
        else:
            self._ensure_reprobe()
            logger.warning(
                "Redis cache unavailable; circuit opened, degrading to DB",
                retry_in=round(self._breaker.cooldown_remaining, 1),
                event_type="cache_circuit_open",
            )
        return ok

    async def _probe(self) -> bool:
        """Background re-probe used by run_reprobe; updates the breaker."""
        try:
            async with self._initialization_lock:
                return await self._attempt_locked()
        except Exception:
            return False

    def _ensure_reprobe(self) -> None:
        """Start the single background recovery loop if not already running."""
        if self._reprobe_task is not None and not self._reprobe_task.done():
            return
        try:
            self._reprobe_task = asyncio.create_task(run_reprobe(self._breaker, self._probe))
        except RuntimeError:
            # No running loop (e.g. called outside async context) — recovery will
            # be retried on the next request-path initialize() instead.
            self._reprobe_task = None

    def _note_op_error(self, error: Exception) -> None:
        """Trip the breaker on a connectivity error so subsequent ops short-circuit."""
        if not isinstance(error, (RedisError, OSError, asyncio.TimeoutError)):
            return  # not a connectivity failure (e.g. JSON) — leave Redis in place
        if self._breaker.is_open():
            return
        self._redis = None
        self._initialized = False
        backoff = self._breaker.record_failure()
        self._ensure_reprobe()
        logger.warning(
            "Redis cache circuit opened after operation failure",
            retry_in=round(backoff, 1),
            error=str(error),
            event_type="cache_circuit_open",
        )

    async def _acquire_redis(self) -> Optional[aioredis.Redis]:
        """Return a usable client, or None to signal 'degrade to DB' — never dials
        while the breaker is open."""
        if not settings.CACHE_ENABLED:
            return None
        if self._breaker.is_open():
            return None
        if self._initialized and self._redis is not None:
            return self._redis
        await self.initialize()
        return self._redis if self._initialized else None

    async def close(self) -> None:
        """Close Redis connections and stop the recovery loop."""
        if self._reprobe_task is not None and not self._reprobe_task.done():
            self._reprobe_task.cancel()
            try:
                await self._reprobe_task
            except (asyncio.CancelledError, Exception):
                pass
        self._reprobe_task = None
        if self._redis:
            await self._redis.close()
        if self._pool:
            await self._pool.disconnect()
        self._initialized = False
        logger.info("Redis cache service closed", event_type="cache_service_close")

    @asynccontextmanager
    async def _get_redis(self):
        """Yield a usable Redis client, or None when caching is unavailable."""
        yield await self._acquire_redis()

    def _make_key(self, entity_type: str, identifier: str) -> str:
        """
        Generate Redis key for caching.

        Args:
            entity_type: Type of entity (api_key, user, session, etc.)
            identifier: Unique identifier for the entity

        Returns:
            Redis key string
        """
        return f"cache:{entity_type}:{identifier}"

    async def get(
        self,
        entity_type: str,
        identifier: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a cached entity.

        Args:
            entity_type: Type of entity
            identifier: Unique identifier

        Returns:
            Cached data as dict, or None if not found
        """
        try:
            async with self._get_redis() as redis:
                if redis is None:
                    # Caching disabled or circuit open - return cache miss
                    return None

                key = self._make_key(entity_type, identifier)
                data = await redis.get(key)

                if data:
                    self._stats["hits"] += 1
                    logger.debug(
                        "Cache hit",
                        entity_type=entity_type,
                        identifier=identifier[:20],
                        event_type="cache_hit",
                    )
                    return json.loads(data)
                else:
                    self._stats["misses"] += 1
                    logger.debug(
                        "Cache miss",
                        entity_type=entity_type,
                        identifier=identifier[:20],
                        event_type="cache_miss",
                    )
                    return None

        except Exception as e:
            self._stats["errors"] += 1
            self._note_op_error(e)
            logger.warning(
                "Cache get failed, returning None",
                entity_type=entity_type,
                identifier=identifier[:20],
                error=str(e),
                event_type="cache_get_error",
            )
            # Fail gracefully - return None on errors
            return None

    async def set(
        self,
        entity_type: str,
        identifier: str,
        data: Dict[str, Any],
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        """
        Set a cached entity.

        Args:
            entity_type: Type of entity
            identifier: Unique identifier
            data: Data to cache (must be JSON-serializable)
            ttl_seconds: Optional TTL override (uses default if not provided)

        Returns:
            True if successful
        """
        try:
            async with self._get_redis() as redis:
                if redis is None:
                    # Caching disabled or circuit open - return success without caching
                    return True

                key = self._make_key(entity_type, identifier)

                # Use entity-specific TTL or provided override
                ttl = ttl_seconds or self.DEFAULT_TTLS.get(entity_type, 300)

                # Serialize to JSON
                serialized = json.dumps(data)

                # Set with TTL
                await redis.setex(key, ttl, serialized)

                self._stats["sets"] += 1
                logger.debug(
                    "Cache set",
                    entity_type=entity_type,
                    identifier=identifier[:20],
                    ttl=ttl,
                    event_type="cache_set",
                )
                return True

        except Exception as e:
            self._stats["errors"] += 1
            self._note_op_error(e)
            logger.warning(
                "Cache set failed",
                entity_type=entity_type,
                identifier=identifier[:20],
                error=str(e),
                event_type="cache_set_error",
            )
            # Fail gracefully - don't raise on cache errors
            return False

    async def delete(
        self,
        entity_type: str,
        identifier: str,
    ) -> bool:
        """
        Delete a cached entity (cache invalidation).

        Args:
            entity_type: Type of entity
            identifier: Unique identifier

        Returns:
            True if successful
        """
        try:
            async with self._get_redis() as redis:
                if redis is None:
                    # Caching disabled or circuit open - return success
                    return True

                key = self._make_key(entity_type, identifier)
                await redis.delete(key)

                logger.debug(
                    "Cache deleted",
                    entity_type=entity_type,
                    identifier=identifier[:20],
                    event_type="cache_delete",
                )
                return True

        except Exception as e:
            self._note_op_error(e)
            logger.warning(
                "Cache delete failed",
                entity_type=entity_type,
                identifier=identifier[:20],
                error=str(e),
                event_type="cache_delete_error",
            )
            return False

    async def get_or_fetch(
        self,
        entity_type: str,
        identifier: str,
        fetch_fn: Callable[[], Any],
        serializer: Optional[Callable[[Any], Dict[str, Any]]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> Optional[Any]:
        """
        Read-through cache pattern: Get from cache, or fetch and cache.

        This is the main method to use for caching - it implements the full
        read-through pattern with automatic cache population.

        Args:
            entity_type: Type of entity
            identifier: Unique identifier
            fetch_fn: Async function to fetch data on cache miss
            serializer: Optional function to serialize fetched data for caching
            ttl_seconds: Optional TTL override

        Returns:
            The entity (either from cache or freshly fetched)
        """
        # Try cache first
        cached = await self.get(entity_type, identifier)
        if cached is not None:
            # Cache hit - return cached data
            # Note: Data is already deserialized from JSON
            return cached

        # Cache miss - fetch from source
        try:
            fetched = await fetch_fn()

            if fetched is None:
                # Source returned None - don't cache
                return None

            # Serialize for caching if serializer provided
            if serializer:
                cache_data = serializer(fetched)
            elif isinstance(fetched, dict):
                # Already a dict - use as-is
                cache_data = fetched
            else:
                # Can't cache this - return without caching
                logger.warning(
                    "Cannot cache non-dict data without serializer",
                    entity_type=entity_type,
                    event_type="cache_skip_no_serializer",
                )
                return fetched

            # Cache the fetched data
            await self.set(entity_type, identifier, cache_data, ttl_seconds)

            return fetched

        except Exception as e:
            logger.error(
                "Fetch function failed in read-through cache",
                entity_type=entity_type,
                error=str(e),
                event_type="cache_fetch_error",
            )
            # Return None on fetch errors
            return None

    async def invalidate_pattern(self, pattern: str) -> int:
        """
        Invalidate all keys matching a pattern.

        WARNING: Use sparingly - SCAN can be expensive on large datasets.

        Args:
            pattern: Redis key pattern (e.g., "cache:user:*")

        Returns:
            Number of keys deleted
        """
        try:
            async with self._get_redis() as redis:
                if redis is None:
                    # Caching disabled or circuit open - nothing to invalidate
                    return 0

                deleted = 0

                # Use SCAN to avoid blocking Redis
                async for key in redis.scan_iter(match=pattern):
                    await redis.delete(key)
                    deleted += 1

                logger.info(
                    "Cache pattern invalidated",
                    pattern=pattern,
                    deleted_count=deleted,
                    event_type="cache_pattern_invalidate",
                )
                return deleted

        except Exception as e:
            self._note_op_error(e)
            logger.error(
                "Cache pattern invalidation failed",
                pattern=pattern,
                error=str(e),
                event_type="cache_pattern_invalidate_error",
            )
            return 0

    def get_stats(self) -> Dict[str, int]:
        """Get cache statistics."""
        total_requests = self._stats["hits"] + self._stats["misses"]
        hit_rate = (
            self._stats["hits"] / total_requests * 100
            if total_requests > 0
            else 0
        )

        return {
            **self._stats,
            "total_requests": total_requests,
            "hit_rate_percent": round(hit_rate, 2),
        }

    async def health_check(self) -> Dict[str, Any]:
        """
        Check Redis cache health. Returns fast (no dial) while the circuit is open
        so /health probes never hang on a Redis outage.
        """
        if not settings.CACHE_ENABLED:
            return {"status": "disabled", "connected": False, "stats": self.get_stats()}

        if self._breaker.is_open():
            return {
                "status": "degraded",
                "connected": False,
                "circuit": "open",
                "retry_in_seconds": round(self._breaker.cooldown_remaining, 1),
                "stats": self.get_stats(),
            }

        try:
            redis = await self._acquire_redis()
            if redis is None:
                return {
                    "status": "degraded",
                    "connected": False,
                    "circuit": "open",
                    "stats": self.get_stats(),
                }
            await redis.ping()
            info = await redis.info("memory")

            return {
                "status": "healthy",
                "connected": True,
                "used_memory": info.get("used_memory_human", "unknown"),
                "stats": self.get_stats(),
            }
        except Exception as e:
            self._note_op_error(e)
            return {
                "status": "unhealthy",
                "connected": False,
                "error": str(e),
                "stats": self.get_stats(),
            }


# Singleton instance
cache_service = CacheService()
