"""
Redis Rate Limiter

Implements sliding window rate limiting using Redis for distributed rate tracking.
Supports both RPM (requests per minute) and TPM (tokens per minute) limits.
"""

import time
import asyncio
from typing import Optional, Tuple
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from redis.asyncio import ConnectionPool

from src.core.config import settings
from src.core.logging_config import get_core_logger

from .types import RateLimitConfig, RateLimitResult, RateLimitStatus

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
    - Graceful degradation on Redis failures
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
            logger.warning(
                "RPM check failed, allowing request",
                error=str(e),
                user_id=user_id,
                event_type="rpm_check_error",
            )
            # Fail open - allow the request if Redis fails
            return 0, config.rpm, True
    
    async def check_and_increment_tpm(
        self,
        user_id: str,
        token_count: int,
        config: RateLimitConfig,
        model_group: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Tuple[int, int, bool]:
        """
        Check and increment the TPM counter.
        
        Args:
            user_id: The user identifier
            token_count: Number of tokens for this request
            config: Rate limit configuration
            model_group: Optional model group for separate limits
            request_id: Unique identifier for this request
            
        Returns:
            Tuple of (current_count, limit, allowed)
        """
        try:
            async with self._get_redis() as redis:
                key = self._get_key(user_id, "tpm", model_group)
                
                # Use fixed window boundaries aligned to clock intervals
                current_time_seconds = int(time.time())
                window_start_seconds = (current_time_seconds // config.window_seconds) * config.window_seconds
                
                # Convert to milliseconds for Redis storage
                current_time = int(time.time() * 1000)
                window_start = window_start_seconds * 1000
                
                entry_id = request_id or str(current_time)
                
                result = await redis.evalsha(
                    self._script_sha,
                    1,
                    key,
                    str(window_start),
                    str(current_time),
                    str(config.tpm),
                    str(token_count),
                    entry_id,
                    str(config.window_seconds + 10),
                )
                
                current_count, allowed, _ = result
                return int(current_count), config.tpm, bool(allowed)
                
        except Exception as e:
            logger.warning(
                "TPM check failed, allowing request",
                error=str(e),
                user_id=user_id,
                token_count=token_count,
                event_type="tpm_check_error",
            )
            return 0, config.tpm, True
    
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

