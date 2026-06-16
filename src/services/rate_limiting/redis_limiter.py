"""
Redis Rate Limiter

Implements FIXED-window rate limiting using Redis for distributed rate tracking.
Supports both RPM (requests per minute) and TPM (tokens per minute) limits.

Windows are clock-aligned (e.g. each minute boundary) and tracked with plain
integer counters keyed by window start:

    ratelimit:rpm:{model_group}:{user_id}:{window_start}
    ratelimit:tpm:{model_group}:{user_id}:{window_start}

This is O(1) per check (INCRBY + GET) instead of the previous per-request ZSET
that required an O(n) ZRANGE + member parsing on every call.
"""

import time
import asyncio
from typing import Optional, Tuple
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from redis.asyncio import ConnectionPool

from src.core.config import settings
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
    - Graceful degradation on Redis failures (fail open)
    - Connection pooling for efficiency
    """

    def __init__(self):
        self._pool: Optional[ConnectionPool] = None
        self._redis: Optional[aioredis.Redis] = None
        self._script_sha: Optional[str] = None
        self._initialized = False
        self._initialization_lock = asyncio.Lock()

    async def initialize(self) -> bool:
        """
        Initialize Redis connection pool.

        Returns:
            True if initialization was successful
        """
        async with self._initialization_lock:
            if self._initialized:
                return True

            try:
                self._pool = ConnectionPool.from_url(
                    settings.REDIS_URL,
                    max_connections=settings.REDIS_MAX_CONNECTIONS,
                    socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
                    socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
                    decode_responses=True,
                )

                self._redis = aioredis.Redis(connection_pool=self._pool)

                # Test connection
                await self._redis.ping()

                # Load Lua script
                self._script_sha = await self._redis.script_load(FIXED_WINDOW_SCRIPT)

                self._initialized = True
                logger.info(
                    "Redis rate limiter initialized",
                    redis_url=settings.REDIS_URL.split("@")[-1],  # Hide password
                    event_type="redis_limiter_init",
                )
                return True

            except Exception as e:
                logger.error(
                    "Failed to initialize Redis rate limiter",
                    error=str(e),
                    event_type="redis_limiter_init_error",
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
        logger.info("Redis rate limiter closed", event_type="redis_limiter_close")

    @asynccontextmanager
    async def _get_redis(self):
        """Get Redis connection with lazy initialization."""
        if not self._initialized:
            await self.initialize()

        if not self._redis:
            raise RuntimeError("Redis not initialized")

        yield self._redis

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
        Check and increment the RPM counter for the current window.

        Args:
            user_id: The user identifier
            config: Rate limit configuration
            model_group: Optional model group for separate limits
            request_id: Unused (kept for signature compatibility)

        Returns:
            Tuple of (current_count, limit, allowed)
        """
        try:
            async with self._get_redis() as redis:
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
        Add tokens to the TPM counter for the current window (no limit check).

        Used to record actual token usage after a request completes.

        Args:
            user_id: The user identifier
            token_count: Number of tokens to add
            config: Rate limit configuration
            model_group: Optional model group
            request_id: Unused (kept for signature compatibility)

        Returns:
            True if successful
        """
        try:
            async with self._get_redis() as redis:
                window_start = self._window_start(config)
                key = self._get_key(user_id, "tpm", model_group, window_start)
                ttl = config.window_seconds + 10

                pipe = redis.pipeline()
                pipe.incrby(key, int(token_count))
                pipe.expire(key, ttl)
                await pipe.execute()

                return True

        except Exception as e:
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
        Get current usage counts for the active window without incrementing.

        Two O(1) GETs against the window-stamped counters.

        Args:
            user_id: The user identifier
            config: Rate limit configuration
            model_group: Optional model group

        Returns:
            Tuple of (rpm_current, tpm_current)
        """
        try:
            async with self._get_redis() as redis:
                window_start = self._window_start(config)
                rpm_key = self._get_key(user_id, "rpm", model_group, window_start)
                tpm_key = self._get_key(user_id, "tpm", model_group, window_start)

                pipe = redis.pipeline()
                pipe.get(rpm_key)
                pipe.get(tpm_key)
                rpm_value, tpm_value = await pipe.execute()

                return int(rpm_value or 0), int(tpm_value or 0)

        except Exception as e:
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
        Reset rate limits for a user by deleting their window-stamped counters.

        Rare admin operation: SCANs the user's rpm/tpm key prefixes (all windows)
        and deletes them. Hot-path checks never SCAN.

        Args:
            user_id: The user identifier
            model_group: Optional model group

        Returns:
            True if successful
        """
        try:
            async with self._get_redis() as redis:
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
            logger.error(
                "Failed to reset user limits",
                error=str(e),
                user_id=user_id,
                event_type="reset_limits_error",
            )
            return False

    async def health_check(self) -> dict:
        """
        Check Redis health status.

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
                    "max_memory": info.get("maxmemory_human", "unknown"),
                }
        except Exception as e:
            return {
                "status": "unhealthy",
                "connected": False,
                "error": str(e),
            }


# Singleton instance
redis_limiter = RedisRateLimiter()
