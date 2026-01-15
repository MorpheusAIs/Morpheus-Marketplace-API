"""
Rate Limiting Service Package

Provides Redis-based rate limiting with support for:
- Requests per minute (RPM)
- Tokens per minute (TPM)
- Model group-specific limits
- User-level rate tracking
"""

from .types import (
    RateLimitResult,
    RateLimitConfig,
    ModelGroupConfig,
    RateLimitStatus,
    RateLimitHeaders,
)
from .rules_service import RateLimitRulesService, rate_limit_rules_service
from .redis_limiter import RedisRateLimiter, redis_limiter
from .rate_limit_service import RateLimitService, rate_limit_service
from .dependencies import (
    RateLimitExceeded,
    check_rate_limit,
    get_user_identifier,
    rate_limit_dependency,
    add_rate_limit_headers,
)

__all__ = [
    # Types
    "RateLimitResult",
    "RateLimitConfig",
    "ModelGroupConfig",
    "RateLimitStatus",
    "RateLimitHeaders",
    # Services
    "RateLimitRulesService",
    "rate_limit_rules_service",
    "RedisRateLimiter",
    "redis_limiter",
    "RateLimitService",
    "rate_limit_service",
    # Dependencies
    "RateLimitExceeded",
    "check_rate_limit",
    "get_user_identifier",
    "rate_limit_dependency",
    "add_rate_limit_headers",
]

