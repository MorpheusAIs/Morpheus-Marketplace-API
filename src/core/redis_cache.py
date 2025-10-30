"""
Redis Cache Module for API Key Validation

This module provides optional Redis caching for API key validation with automatic
graceful degradation. If Redis is unavailable or disabled, it falls back to direct
database queries without impacting functionality.

Two-Way Door Design:
- Can be enabled/disabled via environment variables without code changes
- Automatically falls back to DB if Redis fails
- No changes required in calling code
"""

import asyncio
from functools import lru_cache
from typing import Optional
import redis.asyncio as redis

from .config import settings
from .logging_config import get_api_logger

logger = get_api_logger()


@lru_cache()
def get_redis_client() -> Optional[redis.Redis]:
    """
    Lazily initialize Redis client if configured.
    
    Returns None if:
    - REDIS_URL is not set
    - ENABLE_REDIS_CACHE is false
    - Redis connection fails
    
    This ensures graceful degradation to direct DB queries.
    """
    # Safety check 1: Is Redis URL configured?
    if not settings.REDIS_URL:
        logger.info("Redis caching disabled: REDIS_URL not configured")
        return None
    
    # Safety check 2: Is caching enabled?
    if not settings.ENABLE_REDIS_CACHE:
        logger.info("Redis caching disabled: ENABLE_REDIS_CACHE=false")
        return None
    
    # Attempt to create Redis client
    try:
        client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,  # Auto-decode bytes to strings
            socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
            retry_on_timeout=False,  # Fail fast
            health_check_interval=30,  # Check connection health every 30s
        )
        logger.info("Redis client initialized successfully",
                   redis_url=settings.REDIS_URL.split('@')[-1])  # Log endpoint only, not password
        return client
    except Exception as e:
        logger.error(f"Failed to initialize Redis client: {e}",
                    event_type="redis_init_failed")
        return None


async def get_cached_user_id(api_key_prefix: str) -> Optional[str]:
    """
    Get cached user ID for an API key prefix.
    
    Args:
        api_key_prefix: First 9-15 characters of API key (e.g., "sk-abc123")
    
    Returns:
        User ID as string if cached, None if cache miss or Redis unavailable
    """
    redis_client = get_redis_client()
    if not redis_client:
        return None
    
    cache_key = f"api_key:{api_key_prefix}"
    
    try:
        # Fast timeout: Prefer DB over slow cache
        cached_value = await asyncio.wait_for(
            redis_client.get(cache_key),
            timeout=0.1  # 100ms max
        )
        
        if cached_value:
            logger.debug("Redis cache HIT",
                        cache_key=cache_key,
                        event_type="redis_cache_hit")
            return cached_value
        
        logger.debug("Redis cache MISS",
                    cache_key=cache_key,
                    event_type="redis_cache_miss")
        return None
        
    except asyncio.TimeoutError:
        logger.warning("Redis timeout on GET, falling back to DB",
                      cache_key=cache_key,
                      event_type="redis_timeout")
        return None
    except redis.ConnectionError as e:
        logger.warning(f"Redis connection error: {e}, falling back to DB",
                      event_type="redis_connection_error")
        return None
    except Exception as e:
        logger.error(f"Unexpected Redis error: {e}, falling back to DB",
                    event_type="redis_unexpected_error")
        return None


async def cache_user_id(api_key_prefix: str, user_id: int) -> bool:
    """
    Cache user ID for an API key prefix.
    
    Args:
        api_key_prefix: First 9-15 characters of API key
        user_id: User ID to cache
    
    Returns:
        True if successfully cached, False if caching failed (non-blocking)
    """
    redis_client = get_redis_client()
    if not redis_client:
        return False
    
    cache_key = f"api_key:{api_key_prefix}"
    
    try:
        # Best effort caching: Don't block if it fails
        await asyncio.wait_for(
            redis_client.setex(
                cache_key,
                settings.REDIS_API_KEY_TTL,
                str(user_id)
            ),
            timeout=0.1  # 100ms max
        )
        
        logger.debug("API key cached successfully",
                    cache_key=cache_key,
                    user_id=user_id,
                    ttl=settings.REDIS_API_KEY_TTL,
                    event_type="redis_cache_set")
        return True
        
    except asyncio.TimeoutError:
        logger.warning("Redis timeout on SET, cache write skipped",
                      cache_key=cache_key,
                      event_type="redis_set_timeout")
        return False
    except redis.ConnectionError as e:
        logger.warning(f"Redis connection error on SET: {e}",
                      event_type="redis_set_connection_error")
        return False
    except Exception as e:
        logger.error(f"Unexpected Redis error on SET: {e}",
                    event_type="redis_set_unexpected_error")
        return False


async def invalidate_api_key_cache(api_key_prefix: str) -> bool:
    """
    Invalidate cached API key (e.g., when key is deleted or disabled).
    
    Args:
        api_key_prefix: First 9-15 characters of API key
    
    Returns:
        True if successfully invalidated, False otherwise (non-blocking)
    """
    redis_client = get_redis_client()
    if not redis_client:
        return False
    
    cache_key = f"api_key:{api_key_prefix}"
    
    try:
        await asyncio.wait_for(
            redis_client.delete(cache_key),
            timeout=0.1
        )
        
        logger.info("API key cache invalidated",
                   cache_key=cache_key,
                   event_type="redis_cache_invalidated")
        return True
        
    except Exception as e:
        logger.warning(f"Failed to invalidate cache: {e}",
                      cache_key=cache_key,
                      event_type="redis_invalidate_failed")
        return False


async def get_cached_session(api_key_id: int) -> Optional[dict]:
    """
    Get cached session data for an API key.
    
    Args:
        api_key_id: The API key ID to look up
    
    Returns:
        Session data as dict if cached, None if cache miss or Redis unavailable
    """
    redis_client = get_redis_client()
    if not redis_client:
        return None
    
    cache_key = f"session:api_key:{api_key_id}"
    
    try:
        # Fast timeout: Prefer DB over slow cache
        cached_value = await asyncio.wait_for(
            redis_client.get(cache_key),
            timeout=0.1  # 100ms max
        )
        
        if cached_value:
            logger.debug("Session cache HIT",
                        cache_key=cache_key,
                        api_key_id=api_key_id,
                        event_type="session_cache_hit")
            # Parse JSON back to dict
            import json
            return json.loads(cached_value)
        
        logger.debug("Session cache MISS",
                    cache_key=cache_key,
                    api_key_id=api_key_id,
                    event_type="session_cache_miss")
        return None
        
    except asyncio.TimeoutError:
        logger.warning("Redis timeout on session GET, falling back to DB",
                      cache_key=cache_key,
                      event_type="session_redis_timeout")
        return None
    except redis.ConnectionError as e:
        logger.warning(f"Redis connection error on session GET: {e}, falling back to DB",
                      event_type="session_redis_connection_error")
        return None
    except Exception as e:
        logger.error(f"Unexpected Redis error on session GET: {e}, falling back to DB",
                    event_type="session_redis_unexpected_error")
        return None


async def cache_session(api_key_id: int, session_data: dict, ttl: Optional[int] = None) -> bool:
    """
    Cache session data for an API key.
    
    Args:
        api_key_id: The API key ID
        session_data: Session data to cache (must be JSON-serializable)
        ttl: Optional TTL in seconds (defaults to REDIS_SESSION_TTL from config)
    
    Returns:
        True if successfully cached, False if caching failed (non-blocking)
    """
    redis_client = get_redis_client()
    if not redis_client:
        return False
    
    cache_key = f"session:api_key:{api_key_id}"
    
    # Use provided TTL or default from settings
    if ttl is None:
        ttl = settings.REDIS_SESSION_TTL
    
    try:
        # Best effort caching: Don't block if it fails
        import json
        await asyncio.wait_for(
            redis_client.setex(
                cache_key,
                ttl,
                json.dumps(session_data)
            ),
            timeout=0.1  # 100ms max
        )
        
        logger.debug("Session cached successfully",
                    cache_key=cache_key,
                    api_key_id=api_key_id,
                    session_id=session_data.get('id'),
                    ttl=ttl,
                    event_type="session_cache_set")
        return True
        
    except asyncio.TimeoutError:
        logger.warning("Redis timeout on session SET, cache write skipped",
                      cache_key=cache_key,
                      event_type="session_set_timeout")
        return False
    except redis.ConnectionError as e:
        logger.warning(f"Redis connection error on session SET: {e}",
                      event_type="session_set_connection_error")
        return False
    except Exception as e:
        logger.error(f"Unexpected Redis error on session SET: {e}",
                    event_type="session_set_unexpected_error")
        return False


async def invalidate_session_cache(api_key_id: int) -> bool:
    """
    Invalidate cached session for an API key (e.g., when session is closed or model changed).
    
    Args:
        api_key_id: The API key ID
    
    Returns:
        True if successfully invalidated, False otherwise (non-blocking)
    """
    redis_client = get_redis_client()
    if not redis_client:
        return False
    
    cache_key = f"session:api_key:{api_key_id}"
    
    try:
        await asyncio.wait_for(
            redis_client.delete(cache_key),
            timeout=0.1
        )
        
        logger.info("Session cache invalidated",
                   cache_key=cache_key,
                   api_key_id=api_key_id,
                   event_type="session_cache_invalidated")
        return True
        
    except Exception as e:
        logger.warning(f"Failed to invalidate session cache: {e}",
                      cache_key=cache_key,
                      event_type="session_invalidate_failed")
        return False


async def get_cache_stats() -> dict:
    """
    Get Redis cache statistics for monitoring.
    
    Returns:
        Dictionary with cache stats or empty dict if Redis unavailable
    """
    redis_client = get_redis_client()
    if not redis_client:
        return {
            "enabled": False,
            "reason": "Redis not configured or disabled"
        }
    
    try:
        info = await asyncio.wait_for(
            redis_client.info("stats"),
            timeout=1.0
        )
        
        return {
            "enabled": True,
            "total_commands": info.get("total_commands_processed", 0),
            "keyspace_hits": info.get("keyspace_hits", 0),
            "keyspace_misses": info.get("keyspace_misses", 0),
            "hit_rate": (
                info.get("keyspace_hits", 0) / 
                max(info.get("keyspace_hits", 0) + info.get("keyspace_misses", 0), 1)
            ) * 100
        }
    except Exception as e:
        logger.error(f"Failed to get cache stats: {e}")
        return {
            "enabled": False,
            "error": str(e)
        }

