"""
Redis Cache Service

Provides read-through caching for frequently accessed database entities:
- API keys
- Users
- Active sessions
- JWKS (for JWT validation)

Uses the same Redis instance as rate limiting for efficient resource usage.
"""

import json
import asyncio
from typing import Optional, Any, Dict, Callable, TypeVar
from contextlib import asynccontextmanager
from datetime import timedelta

import redis.asyncio as aioredis
from redis.asyncio import ConnectionPool

from src.core.config import settings
from src.core.logging_config import get_core_logger

logger = get_core_logger()

T = TypeVar('T')


class CacheService:
    """
    Redis-based caching service with read-through pattern support.
    
    Features:
    - Automatic cache-aside (read-through) pattern
    - Configurable TTLs per entity type
    - Graceful degradation on Redis failures
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
        
        # Track cache stats for monitoring
        self._stats = {
            "hits": 0,
            "misses": 0,
            "errors": 0,
            "sets": 0,
        }
    
    async def initialize(self) -> bool:
        """
        Initialize Redis connection pool.
        
        Returns:
            True if initialization was successful
        """
        async with self._initialization_lock:
            if self._initialized:
                return True
            
            # Check if caching is enabled
            if not settings.CACHE_ENABLED:
                logger.info(
                    "Redis caching is disabled (CACHE_ENABLED=false)",
                    event_type="cache_disabled",
                )
                self._initialized = False
                return False
            
            try:
                # Reuse the same Redis URL and connection settings as rate limiter
                self._pool = ConnectionPool.from_url(
                    settings.REDIS_URL,
                    max_connections=settings.REDIS_MAX_CONNECTIONS,
                    socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
                    socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
                    decode_responses=True,  # Automatically decode responses to strings
                )
                
                self._redis = aioredis.Redis(connection_pool=self._pool)
                
                # Test connection
                await self._redis.ping()
                
                self._initialized = True
                logger.info(
                    "Redis cache service initialized",
                    redis_url=settings.REDIS_URL.split("@")[-1],  # Hide password
                    event_type="cache_service_init",
                )
                return True
                
            except Exception as e:
                logger.error(
                    "Failed to initialize Redis cache service",
                    error=str(e),
                    event_type="cache_service_init_error",
                )
                self._initialized = False
                return False
    
    async def close(self) -> None:
        """Close Redis connections."""
        if self._redis:
            await self._redis.close()
        if self._pool:
            await self._pool.disconnect()
        self._initialized = False
        logger.info("Redis cache service closed", event_type="cache_service_close")
    
    @asynccontextmanager
    async def _get_redis(self):
        """Get Redis connection with lazy initialization."""
        if not self._initialized:
            await self.initialize()
        
        if not self._redis:
            # Caching is disabled or initialization failed
            # Return None to allow graceful degradation
            yield None
            return
        
        yield self._redis
    
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
                    # Caching disabled - return cache miss
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
                    # Caching disabled - return success without caching
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
                    # Caching disabled - return success
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
        Check Redis cache health.
        
        Returns:
            Health status dictionary
        """
        try:
            async with self._get_redis() as redis:
                await redis.ping()
                info = await redis.info("memory")
                
                return {
                    "status": "healthy",
                    "connected": True,
                    "used_memory": info.get("used_memory_human", "unknown"),
                    "stats": self.get_stats(),
                }
        except Exception as e:
            return {
                "status": "unhealthy",
                "connected": False,
                "error": str(e),
                "stats": self.get_stats(),
            }


# Singleton instance
cache_service = CacheService()
