"""
Model Switch Rate Limiter Service

Prevents excessive model switching that wastes blockchain transactions and system resources.
Rate limiting is applied per API key, with optional exemptions by user email or user ID.
"""

from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, text
from typing import Optional, Dict, Any
from ..core.config import settings
from ..core.logging_config import get_api_logger

logger = get_api_logger()


class ModelSwitchRateLimitExceeded(Exception):
    """Raised when model switch rate limit is exceeded."""
    
    def __init__(
        self, 
        api_key_prefix: str, 
        user_email: Optional[str],
        limit_type: str, 
        limit_value: int, 
        current_count: int, 
        retry_after_seconds: int
    ):
        self.api_key_prefix = api_key_prefix
        self.user_email = user_email
        self.limit_type = limit_type
        self.limit_value = limit_value
        self.current_count = current_count
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Model switch rate limit exceeded for API key {api_key_prefix} "
            f"(user: {user_email}). {limit_type}: {current_count}/{limit_value}. "
            f"Retry after {retry_after_seconds} seconds."
        )


async def check_model_switch_rate_limit(
    db: AsyncSession,
    api_key_id: int,
    api_key_prefix: str,
    user_id: int,
    user_email: Optional[str],
    current_model: Optional[str],
    requested_model: str
) -> Dict[str, Any]:
    """
    Check if API key has exceeded model switch rate limits.
    
    Args:
        db: Database session
        api_key_id: ID of the API key
        api_key_prefix: Prefix of API key (for logging)
        user_id: ID of the user
        user_email: Email of the user (may be None)
        current_model: Current model ID (None if no active session)
        requested_model: Requested model ID
    
    Returns:
        Dict with:
        - allowed: bool
        - reason: str (if not allowed)
        - limits: dict with current counts
        - retry_after_seconds: int (if not allowed)
    
    Raises:
        ModelSwitchRateLimitExceeded: If rate limit is exceeded
    """
    
    # Skip rate limiting if disabled
    if not settings.MODEL_SWITCH_RATE_LIMIT_ENABLED:
        logger.debug("Model switch rate limiting is disabled system-wide",
                    api_key_prefix=api_key_prefix,
                    event_type="rate_limit_disabled")
        return {"allowed": True, "limits": {}, "disabled": True}
    
    # Check if user is exempt by user ID
    if user_id in settings.MODEL_SWITCH_RATE_LIMIT_EXEMPT_USER_IDS:
        logger.info("User exempt from model switch rate limiting (by user ID)",
                   api_key_prefix=api_key_prefix,
                   user_id=user_id,
                   user_email=user_email,
                   event_type="rate_limit_exempted_user_id")
        return {"allowed": True, "limits": {}, "exempted": True, "exemption_type": "user_id"}
    
    # Check if user is exempt by email
    if user_email and user_email in settings.MODEL_SWITCH_RATE_LIMIT_EXEMPT_EMAILS:
        logger.info("User exempt from model switch rate limiting (by email)",
                   api_key_prefix=api_key_prefix,
                   user_id=user_id,
                   user_email=user_email,
                   event_type="rate_limit_exempted_email")
        return {"allowed": True, "limits": {}, "exempted": True, "exemption_type": "email"}
    
    # If not actually switching models, allow
    if current_model == requested_model:
        logger.debug("No model switch needed, allowing request",
                    api_key_prefix=api_key_prefix,
                    model=requested_model[:20] + "..." if requested_model else None,
                    event_type="rate_limit_no_switch")
        return {"allowed": True, "limits": {}, "no_switch_needed": True}
    
    now = datetime.utcnow()
    
    # Query switches for this API key in different time windows
    # Using raw SQL for clarity and to ensure it works with the table structure
    
    # Check switches in last hour
    hour_ago = now - timedelta(hours=1)
    hour_count_result = await db.execute(
        text("""
            SELECT COUNT(*) 
            FROM api_key_model_switches 
            WHERE api_key_id = :api_key_id 
            AND switched_at >= :hour_ago
        """),
        {"api_key_id": api_key_id, "hour_ago": hour_ago}
    )
    switches_last_hour = hour_count_result.scalar() or 0
    
    # Check switches in last 24 hours
    day_ago = now - timedelta(days=1)
    day_count_result = await db.execute(
        text("""
            SELECT COUNT(*) 
            FROM api_key_model_switches 
            WHERE api_key_id = :api_key_id 
            AND switched_at >= :day_ago
        """),
        {"api_key_id": api_key_id, "day_ago": day_ago}
    )
    switches_last_day = day_count_result.scalar() or 0
    
    # Check recent burst switching (within configured window)
    window_ago = now - timedelta(seconds=settings.MODEL_SWITCH_WINDOW_SECONDS)
    window_count_result = await db.execute(
        text("""
            SELECT COUNT(*) 
            FROM api_key_model_switches 
            WHERE api_key_id = :api_key_id 
            AND switched_at >= :window_ago
        """),
        {"api_key_id": api_key_id, "window_ago": window_ago}
    )
    switches_in_window = window_count_result.scalar() or 0
    
    limits_info = {
        "hourly": {
            "current": switches_last_hour,
            "limit": settings.MODEL_SWITCH_MAX_PER_HOUR
        },
        "daily": {
            "current": switches_last_day,
            "limit": settings.MODEL_SWITCH_MAX_PER_DAY
        },
        "window": {
            "current": switches_in_window,
            "window_seconds": settings.MODEL_SWITCH_WINDOW_SECONDS,
            "description": f"{switches_in_window} switches in last {settings.MODEL_SWITCH_WINDOW_SECONDS}s"
        }
    }
    
    # Check hourly limit
    if switches_last_hour >= settings.MODEL_SWITCH_MAX_PER_HOUR:
        logger.warning("Hourly model switch rate limit exceeded",
                      api_key_prefix=api_key_prefix,
                      api_key_id=api_key_id,
                      user_id=user_id,
                      user_email=user_email,
                      switches_last_hour=switches_last_hour,
                      limit=settings.MODEL_SWITCH_MAX_PER_HOUR,
                      event_type="rate_limit_exceeded_hourly")
        
        # Calculate retry_after (time until oldest switch in hour window expires)
        oldest_switch_result = await db.execute(
            text("""
                SELECT switched_at 
                FROM api_key_model_switches 
                WHERE api_key_id = :api_key_id 
                AND switched_at >= :hour_ago 
                ORDER BY switched_at ASC 
                LIMIT 1
            """),
            {"api_key_id": api_key_id, "hour_ago": hour_ago}
        )
        oldest_switch = oldest_switch_result.scalar()
        retry_after = 3600 - int((now - oldest_switch).total_seconds()) if oldest_switch else 3600
        
        raise ModelSwitchRateLimitExceeded(
            api_key_prefix=api_key_prefix,
            user_email=user_email,
            limit_type="hourly",
            limit_value=settings.MODEL_SWITCH_MAX_PER_HOUR,
            current_count=switches_last_hour,
            retry_after_seconds=retry_after
        )
    
    # Check daily limit
    if switches_last_day >= settings.MODEL_SWITCH_MAX_PER_DAY:
        logger.warning("Daily model switch rate limit exceeded",
                      api_key_prefix=api_key_prefix,
                      api_key_id=api_key_id,
                      user_id=user_id,
                      user_email=user_email,
                      switches_last_day=switches_last_day,
                      limit=settings.MODEL_SWITCH_MAX_PER_DAY,
                      event_type="rate_limit_exceeded_daily")
        
        # Calculate retry_after
        oldest_switch_result = await db.execute(
            text("""
                SELECT switched_at 
                FROM api_key_model_switches 
                WHERE api_key_id = :api_key_id 
                AND switched_at >= :day_ago 
                ORDER BY switched_at ASC 
                LIMIT 1
            """),
            {"api_key_id": api_key_id, "day_ago": day_ago}
        )
        oldest_switch = oldest_switch_result.scalar()
        retry_after = 86400 - int((now - oldest_switch).total_seconds()) if oldest_switch else 86400
        
        raise ModelSwitchRateLimitExceeded(
            api_key_prefix=api_key_prefix,
            user_email=user_email,
            limit_type="daily",
            limit_value=settings.MODEL_SWITCH_MAX_PER_DAY,
            current_count=switches_last_day,
            retry_after_seconds=retry_after
        )
    
    logger.info("Model switch rate limit check passed",
               api_key_prefix=api_key_prefix,
               user_id=user_id,
               user_email=user_email,
               limits=limits_info,
               from_model=current_model[:20] + "..." if current_model else None,
               to_model=requested_model[:20] + "...",
               event_type="rate_limit_check_passed")
    
    return {"allowed": True, "limits": limits_info}


async def record_model_switch(
    db: AsyncSession,
    api_key_id: int,
    user_id: int,
    from_model: Optional[str],
    to_model: str
) -> None:
    """
    Record a model switch in the tracking table.
    
    Args:
        db: Database session
        api_key_id: ID of the API key
        user_id: ID of the user
        from_model: Previous model ID (None if first session)
        to_model: New model ID
    """
    
    if not settings.MODEL_SWITCH_RATE_LIMIT_ENABLED:
        logger.debug("Rate limiting disabled, not recording switch",
                    event_type="rate_limit_recording_skipped")
        return
    
    try:
        await db.execute(
            text("""
                INSERT INTO api_key_model_switches 
                (api_key_id, user_id, from_model, to_model, switched_at)
                VALUES (:api_key_id, :user_id, :from_model, :to_model, NOW())
            """),
            {
                "api_key_id": api_key_id,
                "user_id": user_id,
                "from_model": from_model,
                "to_model": to_model
            }
        )
        await db.commit()
        
        logger.info("Recorded model switch",
                   api_key_id=api_key_id,
                   user_id=user_id,
                   from_model=from_model[:20] + "..." if from_model else None,
                   to_model=to_model[:20] + "...",
                   event_type="model_switch_recorded")
    except Exception as e:
        logger.error("Failed to record model switch",
                    api_key_id=api_key_id,
                    user_id=user_id,
                    error=str(e),
                    event_type="model_switch_record_failed")
        # Don't raise - recording failure shouldn't block the switch
        await db.rollback()
