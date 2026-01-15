"""
Rate Limit Service

Main service that combines rules and Redis limiter to provide
complete rate limiting functionality.
"""

import time
from datetime import datetime, timezone
from typing import Optional
import asyncio

from src.core.config import settings
from src.core.logging_config import get_core_logger

from .types import (
    RateLimitResult,
    RateLimitStatus,
    RateLimitHeaders,
    RateLimitConfig,
)
from .rules_service import rate_limit_rules_service, RateLimitRulesService
from .redis_limiter import redis_limiter, RedisRateLimiter

logger = get_core_logger()


class RateLimitService:
    """
    Main rate limiting service.
    
    Provides:
    - Pre-request rate limit checks (RPM)
    - Token usage tracking (TPM)
    - OpenAI-compatible rate limit headers
    - Model group-aware limiting
    """
    
    def __init__(
        self,
        rules_service: RateLimitRulesService,
        limiter: RedisRateLimiter,
    ):
        self._rules = rules_service
        self._limiter = limiter
        self._initialized = False
        self._initialization_lock = asyncio.Lock()
    
    async def initialize(self) -> bool:
        """Initialize the rate limit service."""
        async with self._initialization_lock:
            if self._initialized:
                return True
            
            self._rules.initialize()
            
            if settings.RATE_LIMIT_ENABLED:
                success = await self._limiter.initialize()
                if not success:
                    logger.warning(
                        "Redis initialization failed, rate limiting will fail open",
                        event_type="rate_limit_init_warning",
                    )
            
            self._initialized = True
            logger.info(
                "Rate limit service initialized",
                enabled=settings.RATE_LIMIT_ENABLED,
                event_type="rate_limit_service_init",
            )
            return True
    
    async def close(self) -> None:
        """Close the rate limit service."""
        await self._limiter.close()
        self._initialized = False
    
    @property
    def is_enabled(self) -> bool:
        """Check if rate limiting is enabled."""
        return settings.RATE_LIMIT_ENABLED
    
    async def check_rate_limit(
        self,
        user_id: str,
        model: Optional[str] = None,
        estimated_tokens: int = 0,
        request_id: Optional[str] = None,
    ) -> RateLimitResult:
        """
        Check if a request is within rate limits.
        
        This should be called before processing a request.
        
        Args:
            user_id: The user identifier (user ID or API key prefix)
            model: The model being requested
            estimated_tokens: Estimated input tokens for the request
            request_id: Unique identifier for this request
            
        Returns:
            RateLimitResult with the check result
        """
        if not self.is_enabled:
            return RateLimitResult(
                allowed=True,
                status=RateLimitStatus.ALLOWED,
            )
        
        if not self._initialized:
            await self.initialize()
        
        # Get rate limit config for this model
        config, model_group = self._rules.get_config_for_model(model)
        
        # Calculate window boundary and reset time
        # Window is aligned to fixed intervals (e.g., each minute boundary)
        current_time = int(time.time())
        window_start = (current_time // config.window_seconds) * config.window_seconds
        reset_at = window_start + config.window_seconds
        retry_after = reset_at - current_time
        
        try:
            # Check RPM limit
            rpm_current, rpm_limit, rpm_allowed = await self._limiter.check_and_increment_rpm(
                user_id=user_id,
                config=config,
                model_group=model_group,
                request_id=request_id,
            )
            
            if not rpm_allowed:
                logger.warning(
                    "Rate limit exceeded (RPM)",
                    user_id=user_id,
                    model=model,
                    model_group=model_group,
                    rpm_current=rpm_current,
                    rpm_limit=rpm_limit,
                    retry_after=retry_after,
                    reset_at=reset_at,
                    event_type="rate_limit_exceeded_rpm",
                )
                
                return RateLimitResult(
                    allowed=False,
                    status=RateLimitStatus.EXCEEDED_RPM,
                    rpm_current=rpm_current,
                    rpm_limit=rpm_limit,
                    rpm_remaining=0,
                    tpm_current=0,
                    tpm_limit=config.tpm,
                    tpm_remaining=config.tpm,
                    reset_at=reset_at,
                    retry_after=retry_after,
                    model_group=model_group,
                )
            
            # Check TPM limit if we have estimated tokens
            # Skip TPM check if tpm=0 (no token limit)
            tpm_current = 0
            tpm_allowed = True
            
            if estimated_tokens > 0 and config.tpm > 0:
                tpm_current, tpm_limit, tpm_allowed = await self._limiter.check_and_increment_tpm(
                    user_id=user_id,
                    token_count=estimated_tokens,
                    config=config,
                    model_group=model_group,
                    request_id=f"{request_id}:est" if request_id else None,
                )
                
                if not tpm_allowed:
                    logger.warning(
                        "Rate limit exceeded (TPM)",
                        user_id=user_id,
                        model=model,
                        model_group=model_group,
                        tpm_current=tpm_current,
                        tpm_limit=config.tpm,
                        estimated_tokens=estimated_tokens,
                        retry_after=retry_after,
                        reset_at=reset_at,
                        event_type="rate_limit_exceeded_tpm",
                    )
                    
                    return RateLimitResult(
                        allowed=False,
                        status=RateLimitStatus.EXCEEDED_TPM,
                        rpm_current=rpm_current,
                        rpm_limit=rpm_limit,
                        rpm_remaining=max(0, rpm_limit - rpm_current),
                        tpm_current=tpm_current,
                        tpm_limit=config.tpm,
                        tpm_remaining=0,
                        reset_at=reset_at,
                        retry_after=retry_after,
                        model_group=model_group,
                    )
            
            # All checks passed
            return RateLimitResult(
                allowed=True,
                status=RateLimitStatus.ALLOWED,
                rpm_current=rpm_current,
                rpm_limit=rpm_limit,
                rpm_remaining=max(0, rpm_limit - rpm_current),
                tpm_current=tpm_current,
                tpm_limit=config.tpm,
                tpm_remaining=max(0, config.tpm - tpm_current),
                reset_at=reset_at,
                retry_after=0,
                model_group=model_group,
            )
            
        except Exception as e:
            logger.error(
                "Rate limit check failed",
                error=str(e),
                user_id=user_id,
                model=model,
                event_type="rate_limit_check_error",
            )
            
            # Fail open - allow the request
            return RateLimitResult(
                allowed=True,
                status=RateLimitStatus.ERROR,
                error_message=str(e),
            )
    
    async def record_token_usage(
        self,
        user_id: str,
        input_tokens: int,
        output_tokens: int,
        model: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> bool:
        """
        Record actual token usage after a request completes.
        
        This adjusts the TPM counter based on actual usage vs estimated.
        
        Args:
            user_id: The user identifier
            input_tokens: Actual input tokens used
            output_tokens: Actual output tokens generated
            model: The model that was used
            request_id: Unique identifier for the request
            
        Returns:
            True if recording was successful
        """
        if not self.is_enabled:
            return True
        
        if not self._initialized:
            await self.initialize()
        
        config, model_group = self._rules.get_config_for_model(model)
        total_tokens = input_tokens + output_tokens
        
        try:
            await self._limiter.add_tokens(
                user_id=user_id,
                token_count=total_tokens,
                config=config,
                model_group=model_group,
                request_id=request_id,
            )
            
            logger.debug(
                "Token usage recorded",
                user_id=user_id,
                model=model,
                model_group=model_group,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                event_type="token_usage_recorded",
            )
            
            return True
            
        except Exception as e:
            logger.warning(
                "Failed to record token usage",
                error=str(e),
                user_id=user_id,
                event_type="token_usage_record_error",
            )
            return False
    
    async def get_usage_status(
        self,
        user_id: str,
        model: Optional[str] = None,
    ) -> RateLimitResult:
        """
        Get current rate limit usage status for a user.
        
        Args:
            user_id: The user identifier
            model: Optional model for model-specific limits
            
        Returns:
            RateLimitResult with current usage
        """
        if not self.is_enabled:
            return RateLimitResult(
                allowed=True,
                status=RateLimitStatus.ALLOWED,
            )
        
        if not self._initialized:
            await self.initialize()
        
        config, model_group = self._rules.get_config_for_model(model)
        
        try:
            rpm_current, tpm_current = await self._limiter.get_current_usage(
                user_id=user_id,
                config=config,
                model_group=model_group,
            )
            
            # Calculate window boundary and reset time
            current_time = int(time.time())
            window_start = (current_time // config.window_seconds) * config.window_seconds
            reset_at = window_start + config.window_seconds
            
            # When tpm=0, there's no token limit (always allowed for tokens)
            tpm_allowed = config.tpm == 0 or tpm_current < config.tpm
            tpm_remaining = 0 if config.tpm == 0 else max(0, config.tpm - tpm_current)
            
            return RateLimitResult(
                allowed=rpm_current < config.rpm and tpm_allowed,
                status=RateLimitStatus.ALLOWED,
                rpm_current=rpm_current,
                rpm_limit=config.rpm,
                rpm_remaining=max(0, config.rpm - rpm_current),
                tpm_current=tpm_current,
                tpm_limit=config.tpm,
                tpm_remaining=tpm_remaining,
                reset_at=reset_at,
                model_group=model_group,
            )
            
        except Exception as e:
            logger.warning(
                "Failed to get usage status",
                error=str(e),
                user_id=user_id,
                event_type="get_usage_status_error",
            )
            
            return RateLimitResult(
                allowed=True,
                status=RateLimitStatus.ERROR,
                error_message=str(e),
            )
    
    def create_rate_limit_headers(
        self,
        result: RateLimitResult,
    ) -> RateLimitHeaders:
        """
        Create OpenAI-compatible rate limit headers.
        
        Args:
            result: The rate limit result
            
        Returns:
            RateLimitHeaders object
        """
        reset_time = datetime.fromtimestamp(result.reset_at, tz=timezone.utc)
        reset_iso = reset_time.isoformat()
        
        return RateLimitHeaders(
            x_ratelimit_limit_requests=result.rpm_limit,
            x_ratelimit_limit_tokens=result.tpm_limit,
            x_ratelimit_remaining_requests=result.rpm_remaining,
            x_ratelimit_remaining_tokens=result.tpm_remaining,
            x_ratelimit_reset_requests=reset_iso,
            x_ratelimit_reset_tokens=reset_iso,
            retry_after=result.retry_after if result.is_rate_limited else None,
        )
    
    async def reset_user_limits(
        self,
        user_id: str,
        model: Optional[str] = None,
    ) -> bool:
        """
        Reset rate limits for a user.
        
        Args:
            user_id: The user identifier
            model: Optional model for model-specific limits
            
        Returns:
            True if successful
        """
        if not self._initialized:
            await self.initialize()
        
        _, model_group = self._rules.get_config_for_model(model)
        return await self._limiter.reset_user_limits(user_id, model_group)
    
    def get_rules_info(self) -> dict:
        """Get information about configured rate limit rules."""
        return self._rules.get_all_rules_info()
    
    async def health_check(self) -> dict:
        """
        Get health status of the rate limiting service.
        
        Returns:
            Health status dictionary
        """
        redis_health = await self._limiter.health_check()
        
        return {
            "enabled": self.is_enabled,
            "initialized": self._initialized,
            "redis": redis_health,
            "rules": {
                "default_rpm": self._rules.default_config.rpm if self._rules._initialized else None,
                "default_tpm": self._rules.default_config.tpm if self._rules._initialized else None,
                "model_groups_count": len(self._rules.model_groups) if self._rules._initialized else 0,
            },
        }


# Singleton instance
rate_limit_service = RateLimitService(
    rules_service=rate_limit_rules_service,
    limiter=redis_limiter,
)

