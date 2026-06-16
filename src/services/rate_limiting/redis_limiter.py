"""
Redis Rate Limiter

Implements FIXED-window rate limiting using Redis for distributed rate tracking.
Supports both RPM (requests per minute) and TPM (tokens per minute) limits.

Windows are clock-aligned and tracked with plain integer counters keyed by
window start (ratelimit:{rpm|tpm}:{group}:{user}:{window_start}): O(1) per check
(INCRBY + GET) instead of a per-request ZSET.

Resilience: a circuit breaker guards every Redis access. The limiter already
fails open by design, so during a timeout-mode Redis outage the breaker opens
after the first failure and every check returns "allowed" *immediately* — no
per-request connect timeouts, no global init lock contention on the hot path.
A single background task re-probes Redis with exponential backoff and closes the
breaker on recovery, so reconnection attempts never run on the request path.
The limiter also uses much tighter socket timeouts than the cache (a rate-limit
check must never be allowed to block for seconds).
"""

import time
import asyncio
from typing import Optional, Tuple
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from redis.asyncio import ConnectionPool
from redis.exceptions import RedisError

from src.core.config import settings
from src.core.circuit_breaker import CircuitBreaker, run_reprobe
from src.core.logging_config import get_core_logger

from .types import RateLimitConfig

logger = get_core_logger()


# Lua script for fixed-window rate limiting on a plain integer counter.
# Atomically: read the current count, reject if adding `increment` would exceed
# `limit` (check-before-add, so no rollback is ever needed), otherwise INCRBY
# and refresh the TTL. The key is window-stamped, so it represents exactly the
# current clock-aligned window and self-expires.
# Returns: {current_count, allowed (1/0), ttl}
FIXED_WINDOW_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local increment = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

local current = tonumber(redis.call('GET', key) or '0')

if current + increment > limit then
    return {current, 0, redis.call('TTL', key)}
end

local updated = redis.call('INCRBY', key, increment)
redis.call('EXPIRE', key, ttl)
return {updated, 1, ttl}
"""


class RedisRateLimiter:
    """
    Redis-based fixed-window rate limiter.

    Provides:
    - Atomic O(1) rate limit checks using a Lua INCRBY counter
    - Support for both RPM and TPM limits
    - Circuit-breaker graceful degradation on Redis failures (fail open)
    - Connection pooling for efficiency
    """

    def __init__(self):
        self._pool: Optional[ConnectionPool] = None
        self._redis: Optional[aioredis.Redis] = None
        self._script_sha: Optional[str] = None
        self._initialized = False
        self._initialization_lock = asyncio.Lock()

        # Circuit breaker: open => skip Redis and fail open immediately.
        self._breaker = CircuitBreaker("rate_limiter", initial_backoff=0.5, max_backoff=10.0)
        self._reprobe_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ #
    # Connection lifecycle + circuit breaker
    # ------------------------------------------------------------------ #

    async def _connect_locked(self) -> bool:
        """Build (once) or reuse the pool, verify it, and load the Lua script.
        Assumes the init lock is held. Returns True on success."""
        try:
            if self._pool is None:
                # Pool is built once and reused — redis-py reconnects on demand,
                # so we must not rebuild it per attempt.
                self._pool = ConnectionPool.from_url(
                    settings.REDIS_URL,
                    max_connections=settings.REDIS_MAX_CONNECTIONS,
                    socket_timeout=settings.RATE_LIMIT_REDIS_SOCKET_TIMEOUT,
                    socket_connect_timeout=settings.RATE_LIMIT_REDIS_SOCKET_CONNECT_TIMEOUT,
                    decode_responses=True,
                    health_check_interval=30,
                    retry_on_timeout=True,
                )
            self._redis = aioredis.Redis(connection_pool=self._pool)
            await self._redis.ping()
            self._script_sha = await self._redis.script_load(FIXED_WINDOW_SCRIPT)
            self._initialized = True
            return True
        except Exception as e:
            # Drop the broken client so it is never yielded to the request path.
            self._redis = None
            self._initialized = False
            logger.error(
                "Failed to connect Redis rate limiter",
                error=str(e),
                event_type="redis_limiter_init_error",
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
        Initialize the Redis connection pool. Safe to call at startup; also used
        as the single-flight (re)connect on the request path.

        Returns True if a usable connection is established.
        """
        async with self._initialization_lock:
            if self._initialized:
                return True
            if self._breaker.is_open():
                return False
            ok = await self._attempt_locked()

        if ok:
            logger.info(
                "Redis rate limiter initialized",
                redis_url=settings.REDIS_URL.split("@")[-1],  # Hide password
                event_type="redis_limiter_init",
            )
        else:
            self._ensure_reprobe()
            logger.warning(
                "Redis rate limiter unavailable; circuit opened, failing open",
                retry_in=round(self._breaker.cooldown_remaining, 1),
                event_type="redis_limiter_circuit_open",
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
            self._reprobe_task = None

    def _note_op_error(self, error: Exception) -> None:
        """Trip the breaker on a connectivity error so subsequent checks fail open fast."""
        if not isinstance(error, (RedisError, OSError, asyncio.TimeoutError)):
            return
        if self._breaker.is_open():
            return
        self._redis = None
        self._initialized = False
        backoff = self._breaker.record_failure()
        self._ensure_reprobe()
        logger.warning(
            "Redis rate limiter circuit opened after operation failure",
            retry_in=round(backoff, 1),
            error=str(error),
            event_type="redis_limiter_circuit_open",
        )

    async def _acquire_redis(self) -> Optional[aioredis.Redis]:
        """Return a usable client, or None to signal 'fail open' — never dials
        while the breaker is open."""
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
        logger.info("Redis rate limiter closed", event_type="redis_limiter_close")

    @asynccontextmanager
    async def _get_redis(self):
        """Yield a usable Redis client, or None when the limiter is degraded."""
        yield await self._acquire_redis()

    @staticmethod
    def _window_start(config: RateLimitConfig) -> int:
        """Clock-aligned start (epoch seconds) of the current fixed window."""
        now = int(time.time())
        return (now // config.window_seconds) * config.window_seconds

    def _get_key(
        self,
        user_id: str,
        key_type: str,
        model_group: Optional[str] = None,
        window_start: Optional[int] = None,
    ) -> str:
        """
        Generate the window-stamped Redis key for rate limiting.

        Args:
            user_id: The user identifier
            key_type: Either 'rpm' or 'tpm'
            model_group: Optional model group name
            window_start: Clock-aligned window start (epoch seconds). Omitted only
                          when building a SCAN prefix (see reset_user_limits).

        Returns:
            Redis key string
        """
        if model_group:
            base = f"ratelimit:{key_type}:{model_group}:{user_id}"
        else:
            base = f"ratelimit:{key_type}:{user_id}"
        if window_start is None:
            return base
        return f"{base}:{window_start}"

    async def check_and_increment_rpm(
        self,
        user_id: str,
        config: RateLimitConfig,
        model_group: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Tuple[int, int, bool]:
        """
        Check and increment the RPM counter.

        Args:
            user_id: The user identifier
            config: Rate limit configuration
            model_group: Optional model group for separate limits
            request_id: Unique identifier for this request

        Returns:
            Tuple of (current_count, limit, allowed)
        """
        try:
            async with self._get_redis() as redis:
                if redis is None:
                    # Degraded (circuit open) - fail open
                    return 0, config.rpm, True

                window_start = self._window_start(config)
                key = self._get_key(user_id, "rpm", model_group, window_start)
                ttl = config.window_seconds + 10  # outlive the window by a small buffer

                result = await redis.evalsha(
                    self._script_sha,
                    1,  # Number of keys
                    key,
                    str(config.rpm),  # limit
                    "1",              # increment by 1 for RPM
                    str(ttl),
                )

                current_count, allowed, _ = result
                return int(current_count), config.rpm, bool(allowed)

        except Exception as e:
            self._note_op_error(e)
            logger.warning(
                "RPM check failed, allowing request",
                error=str(e),
                user_id=user_id,
                event_type="rpm_check_error",
            )
            # Fail open - allow the request if Redis fails
            return 0, config.rpm, True

    async def add_tokens(
        self,
        user_id: str,
        token_count: int,
        config: RateLimitConfig,
        model_group: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> bool:
        """
        Add tokens to the TPM counter without checking limits.

        Used to record actual token usage after a request completes.

        Args:
            user_id: The user identifier
            token_count: Number of tokens to add
            config: Rate limit configuration
            model_group: Optional model group
            request_id: Unique identifier for this request

        Returns:
            True if successful
        """
        try:
            async with self._get_redis() as redis:
                if redis is None:
                    # Degraded (circuit open) - skip recording
                    return False

                window_start = self._window_start(config)
                key = self._get_key(user_id, "tpm", model_group, window_start)
                ttl = config.window_seconds + 10

                pipe = redis.pipeline()
                pipe.incrby(key, int(token_count))
                pipe.expire(key, ttl)
                await pipe.execute()

                return True

        except Exception as e:
            self._note_op_error(e)
            logger.warning(
                "Failed to add tokens",
                error=str(e),
                user_id=user_id,
                token_count=token_count,
                event_type="add_tokens_error",
            )
            return False

    async def get_current_usage(
        self,
        user_id: str,
        config: RateLimitConfig,
        model_group: Optional[str] = None,
    ) -> Tuple[int, int]:
        """
        Get current usage counts without incrementing.

        Args:
            user_id: The user identifier
            config: Rate limit configuration
            model_group: Optional model group

        Returns:
            Tuple of (rpm_current, tpm_current)
        """
        try:
            async with self._get_redis() as redis:
                if redis is None:
                    # Degraded (circuit open) - report no usage
                    return 0, 0

                window_start = self._window_start(config)
                rpm_key = self._get_key(user_id, "rpm", model_group, window_start)
                tpm_key = self._get_key(user_id, "tpm", model_group, window_start)

                pipe = redis.pipeline()
                pipe.get(rpm_key)
                pipe.get(tpm_key)
                rpm_value, tpm_value = await pipe.execute()

                return int(rpm_value or 0), int(tpm_value or 0)

        except Exception as e:
            self._note_op_error(e)
            logger.warning(
                "Failed to get current usage",
                error=str(e),
                user_id=user_id,
                event_type="get_usage_error",
            )
            return 0, 0

    async def reset_user_limits(
        self,
        user_id: str,
        model_group: Optional[str] = None,
    ) -> bool:
        """
        Reset rate limits for a user.

        Args:
            user_id: The user identifier
            model_group: Optional model group

        Returns:
            True if successful
        """
        try:
            async with self._get_redis() as redis:
                if redis is None:
                    return False

                # Prefix without the window suffix, plus '*' to match every window.
                prefixes = [
                    self._get_key(user_id, "rpm", model_group),
                    self._get_key(user_id, "tpm", model_group),
                ]
                deleted = 0
                for prefix in prefixes:
                    async for key in redis.scan_iter(match=f"{prefix}:*"):
                        await redis.delete(key)
                        deleted += 1

                logger.info(
                    "User rate limits reset",
                    user_id=user_id,
                    model_group=model_group,
                    deleted_keys=deleted,
                    event_type="rate_limits_reset",
                )
                return True

        except Exception as e:
            self._note_op_error(e)
            logger.error(
                "Failed to reset user limits",
                error=str(e),
                user_id=user_id,
                event_type="reset_limits_error",
            )
            return False

    async def health_check(self) -> dict:
        """
        Check Redis health status. Returns fast (no dial) while the circuit is
        open so /health probes never hang on a Redis outage.
        """
        if self._breaker.is_open():
            return {
                "status": "degraded",
                "connected": False,
                "circuit": "open",
                "retry_in_seconds": round(self._breaker.cooldown_remaining, 1),
            }

        try:
            redis = await self._acquire_redis()
            if redis is None:
                return {"status": "degraded", "connected": False, "circuit": "open"}
            await redis.ping()
            info = await redis.info("memory")

            return {
                "status": "healthy",
                "connected": True,
                "used_memory": info.get("used_memory_human", "unknown"),
                "max_memory": info.get("maxmemory_human", "unknown"),
            }
        except Exception as e:
            self._note_op_error(e)
            return {
                "status": "unhealthy",
                "connected": False,
                "error": str(e),
            }


# Singleton instance
redis_limiter = RedisRateLimiter()
