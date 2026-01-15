"""
Rate Limiting Type Definitions

Defines data structures for rate limiting configuration and results.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List
from enum import Enum


class RateLimitStatus(str, Enum):
    """Status of a rate limit check."""
    ALLOWED = "allowed"
    EXCEEDED_RPM = "exceeded_rpm"
    EXCEEDED_TPM = "exceeded_tpm"
    ERROR = "error"


@dataclass
class RateLimitConfig:
    """
    Configuration for rate limits.
    
    Attributes:
        rpm: Requests per minute limit
        tpm: Tokens per minute limit (input + output)
        window_seconds: Time window for rate limiting (default 60s)
    """
    rpm: int
    tpm: int
    window_seconds: int = 60
    
    def __post_init__(self):
        if self.rpm < 0:
            raise ValueError("RPM must be non-negative")
        if self.tpm < 0:
            raise ValueError("TPM must be non-negative")
        if self.window_seconds <= 0:
            raise ValueError("Window seconds must be positive")


@dataclass
class ModelGroupConfig:
    """
    Configuration for a model group with specific rate limits.
    
    Attributes:
        name: Unique identifier for this model group
        rpm: Requests per minute limit for this group
        tpm: Tokens per minute limit for this group
        models: List of model names/patterns that belong to this group
        priority: Higher priority groups are matched first (default 0)
        description: Human-readable description of this group
    """
    name: str
    rpm: int
    tpm: int
    models: List[str] = field(default_factory=list)
    priority: int = 0
    description: str = ""
    
    def matches_model(self, model_name: str) -> bool:
        """
        Check if a model name matches this group.
        
        Supports exact matches and prefix matches (ending with *).
        
        Args:
            model_name: The model name to check
            
        Returns:
            True if the model matches this group
        """
        if not model_name:
            return False
            
        model_lower = model_name.lower()
        
        for pattern in self.models:
            pattern_lower = pattern.lower()
            
            # Exact match
            if pattern_lower == model_lower:
                return True
            
            # Prefix match (pattern ends with *)
            if pattern_lower.endswith("*"):
                prefix = pattern_lower[:-1]
                if model_lower.startswith(prefix):
                    return True
            
            # Contains match (for flexibility)
            if pattern_lower in model_lower or model_lower in pattern_lower:
                return True
        
        return False


@dataclass
class RateLimitResult:
    """
    Result of a rate limit check.
    
    Attributes:
        allowed: Whether the request is allowed
        status: Detailed status of the rate limit check
        rpm_current: Current requests in the window
        rpm_limit: Maximum requests allowed
        rpm_remaining: Remaining requests
        tpm_current: Current tokens in the window
        tpm_limit: Maximum tokens allowed
        tpm_remaining: Remaining tokens
        reset_at: Unix timestamp when the window resets
        retry_after: Seconds until the request can be retried (if rate limited)
        model_group: The model group that was matched (if any)
        error_message: Error message if status is ERROR
    """
    allowed: bool
    status: RateLimitStatus
    rpm_current: int = 0
    rpm_limit: int = 0
    rpm_remaining: int = 0
    tpm_current: int = 0
    tpm_limit: int = 0
    tpm_remaining: int = 0
    reset_at: int = 0
    retry_after: int = 0
    model_group: Optional[str] = None
    error_message: Optional[str] = None
    
    @property
    def is_rate_limited(self) -> bool:
        """Check if the request was rate limited."""
        return self.status in (RateLimitStatus.EXCEEDED_RPM, RateLimitStatus.EXCEEDED_TPM)


@dataclass
class RateLimitHeaders:
    """
    HTTP headers for rate limit information (OpenAI compatible).
    
    These headers follow OpenAI's rate limit header conventions.
    """
    x_ratelimit_limit_requests: int
    x_ratelimit_limit_tokens: int
    x_ratelimit_remaining_requests: int
    x_ratelimit_remaining_tokens: int
    x_ratelimit_reset_requests: str  # ISO timestamp
    x_ratelimit_reset_tokens: str    # ISO timestamp
    retry_after: Optional[int] = None
    
    def to_dict(self) -> Dict[str, str]:
        """Convert to a dictionary of header name -> value."""
        headers = {
            "X-RateLimit-Limit-Requests": str(self.x_ratelimit_limit_requests),
            "X-RateLimit-Limit-Tokens": str(self.x_ratelimit_limit_tokens),
            "X-RateLimit-Remaining-Requests": str(self.x_ratelimit_remaining_requests),
            "X-RateLimit-Remaining-Tokens": str(self.x_ratelimit_remaining_tokens),
            "X-RateLimit-Reset-Requests": self.x_ratelimit_reset_requests,
            "X-RateLimit-Reset-Tokens": self.x_ratelimit_reset_tokens,
        }
        
        if self.retry_after is not None:
            headers["Retry-After"] = str(self.retry_after)
        
        return headers

