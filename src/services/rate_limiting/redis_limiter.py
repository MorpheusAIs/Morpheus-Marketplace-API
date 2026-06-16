"""
Redis Rate Limiter

Implements sliding window rate limiting using Redis for distributed rate tracking.
Supports both RPM (requests per minute) and TPM (tokens per minute) limits.

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


# Lua script for fixed window rate limiting
# This script atomically:
# 1. Removes entries from previous windows (before window_start)
# 2. Counts current entries/tokens in the current window
# 3. Adds new entry if under limit
# Returns: (current_count, allowed, ttl)
#
# For sorted sets:
# - Score = timestamp in milliseconds (for time-based window management)
# - Member = "increment:entry_id:timestamp" (stores the count value in the member name)
#
# Window is FIXED: all entries with score >= window_start are in the current window
FIXED_WINDOW_SCRIPT = """
local key = KEYS[1]
local window_start = tonumber(ARGV[1])
local current_time = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local increment = tonumber(ARGV[4])
local entry_id = ARGV[5]
local ttl = tonumber(ARGV[6])

-- Remove entries BEFORE the current window (score < window_start)
-- Using (window_start - 1) to exclude entries exactly at window_start
redis.call('ZREMRANGEBYSCORE', key, 0, window_start - 1)

-- Get current count by parsing increment values from member names
-- Member format: "increment:entry_id:timestamp"
local current = 0
local members = redis.call('ZRANGE', key, 0, -1)
for i, member in ipairs(members) do
    -- Extract the increment value from the member name (first part before :)
    local inc = tonumber(string.match(member, "^(%d+):"))
    if inc then
        current = current + inc
    end
end

-- Check if we're over the limit
if current + increment > limit then
    return {current, 0, redis.call('TTL', key)}
end

-- Add the new entry with timestamp as score, increment in member name
-- Member format: "increment:entry_id:timestamp" to ensure uniqueness
local member = increment .. ':' .. entry_id .. ':' .. current_time
redis.call('ZADD', key, current_time, member)

-- Set expiration on the key
redis.call('EXPIRE', key, ttl)

-- Return current count (after adding) and allowed flag
return {current + increment, 1, ttl}
"""


class RedisRateLimiter:
    """
    Redis-based rate limiter using sliding window algorithm.

    Provides:
    - Atomic rate limit checks using Lua scripts
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

    def _get_key(self, user_id: str, key_type: str, model_group: Optional[str] = None) -> str:
        """
        Generate Redis key for rate limiting.

        Args:
            user_id: The user identifier
            key_type: Either 'rpm' or 'tpm'
            model_group: Optional model group name

        Returns:
            Redis key string
        """
        if model_group:
            return f"ratelimit:{key_type}:{model_group}:{user_id}"
        return f"ratelimit:{key_type}:{user_id}"

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

                key = self._get_key(user_id, "rpm", model_group)

                # Use fixed window boundaries aligned to clock intervals
                # This ensures the window resets at predictable times (e.g., start of each minute)
                current_time_seconds = int(time.time())
                window_start_seconds = (current_time_seconds // config.window_seconds) * config.window_seconds

                # Convert to milliseconds for Redis storage
                current_time = int(time.time() * 1000)
                window_start = window_start_seconds * 1000  # Window boundary in milliseconds

                entry_id = request_id or str(current_time)

                result = await redis.evalsha(
                    self._script_sha,
                    1,  # Number of keys
                    key,
                    str(window_start),
                    str(current_time),
                    str(config.rpm),
                    "1",  # Increment by 1 for RPM
                    entry_id,
                    str(config.window_seconds + 10),  # TTL with buffer
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

                key = self._get_key(user_id, "tpm", model_group)
                current_time = int(time.time() * 1000)
                entry_id = f"{request_id or current_time}:actual"

                # Add tokens with timestamp as score, token count in member name
                # Member format: "token_count:entry_id:timestamp"
                member = f"{token_count}:{entry_id}:{current_time}"
                await redis.zadd(key, {member: current_time})
                await redis.expire(key, config.window_seconds + 10)

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

                # Use fixed window boundaries aligned to clock intervals
                current_time_seconds = int(time.time())
                window_start_seconds = (current_time_seconds // config.window_seconds) * config.window_seconds
                window_start = window_start_seconds * 1000  # Convert to milliseconds

                rpm_key = self._get_key(user_id, "rpm", model_group)
                tpm_key = self._get_key(user_id, "tpm", model_group)

                # Clean up old entries (before current window) and get members
                pipe = redis.pipeline()
                pipe.zremrangebyscore(rpm_key, 0, window_start - 1)  # Remove entries before window start
                pipe.zremrangebyscore(tpm_key, 0, window_start - 1)
                pipe.zrange(rpm_key, 0, -1)  # Get all RPM members
                pipe.zrange(tpm_key, 0, -1)  # Get all TPM members

                results = await pipe.execute()

                rpm_members = results[2] or []
                tpm_members = results[3] or []

                # For RPM, each entry represents 1 request
                rpm_count = len(rpm_members)

                # For TPM, parse increment from member names (format: "increment:entry_id:timestamp")
                tpm_count = 0
                for member in tpm_members:
                    try:
                        increment = int(member.split(':')[0])
                        tpm_count += increment
                    except (ValueError, IndexError):
                        # Fallback: count as 1 if parsing fails
                        tpm_count += 1

                return int(rpm_count), int(tpm_count)

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

                rpm_key = self._get_key(user_id, "rpm", model_group)
                tpm_key = self._get_key(user_id, "tpm", model_group)

                await redis.delete(rpm_key, tpm_key)

                logger.info(
                    "User rate limits reset",
                    user_id=user_id,
                    model_group=model_group,
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
