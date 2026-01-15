"""
Rate Limit Schemas

Pydantic schemas for rate limiting API responses and configurations.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class RateLimitUsageResponse(BaseModel):
    """Response schema for rate limit usage status."""
    
    allowed: bool = Field(
        description="Whether the user is currently within rate limits"
    )
    rpm_current: int = Field(
        description="Current requests in the time window"
    )
    rpm_limit: int = Field(
        description="Maximum requests allowed per window"
    )
    rpm_remaining: int = Field(
        description="Remaining requests in the current window"
    )
    tpm_current: int = Field(
        description="Current tokens used in the time window"
    )
    tpm_limit: int = Field(
        description="Maximum tokens allowed per window"
    )
    tpm_remaining: int = Field(
        description="Remaining tokens in the current window"
    )
    reset_at: int = Field(
        description="Unix timestamp when the window resets"
    )
    model_group: Optional[str] = Field(
        default=None,
        description="Model group for which these limits apply"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "allowed": True,
                "rpm_current": 15,
                "rpm_limit": 60,
                "rpm_remaining": 45,
                "tpm_current": 25000,
                "tpm_limit": 100000,
                "tpm_remaining": 75000,
                "reset_at": 1705320000,
                "model_group": "standard"
            }
        }


class ModelGroupRulesResponse(BaseModel):
    """Response schema for a single model group configuration."""
    
    name: str = Field(description="Unique identifier for this model group")
    rpm: int = Field(description="Requests per minute limit")
    tpm: int = Field(description="Tokens per minute limit")
    models: List[str] = Field(description="Model name patterns in this group")
    priority: int = Field(description="Priority for matching (higher = matched first)")
    description: str = Field(default="", description="Human-readable description")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "standard",
                "rpm": 60,
                "tpm": 100000,
                "models": ["gpt-3.5*", "mistral*"],
                "priority": 50,
                "description": "Standard models with moderate limits"
            }
        }


class RateLimitRulesResponse(BaseModel):
    """Response schema for rate limit rules configuration."""
    
    enabled: bool = Field(
        description="Whether rate limiting is enabled"
    )
    default: Dict[str, Any] = Field(
        description="Default rate limit configuration"
    )
    model_groups: List[ModelGroupRulesResponse] = Field(
        description="Model group-specific rate limit configurations"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "enabled": True,
                "default": {
                    "rpm": 60,
                    "tpm": 100000,
                    "window_seconds": 60
                },
                "model_groups": [
                    {
                        "name": "premium",
                        "rpm": 30,
                        "tpm": 50000,
                        "models": ["gpt-4*"],
                        "priority": 100,
                        "description": "Premium models with lower limits"
                    }
                ]
            }
        }


class RateLimitHealthResponse(BaseModel):
    """Response schema for rate limit health check."""
    
    enabled: bool = Field(description="Whether rate limiting is enabled")
    initialized: bool = Field(description="Whether the service is initialized")
    redis: Dict[str, Any] = Field(description="Redis connection health")
    rules: Dict[str, Any] = Field(description="Rules configuration summary")

    class Config:
        json_schema_extra = {
            "example": {
                "enabled": True,
                "initialized": True,
                "redis": {
                    "status": "healthy",
                    "connected": True,
                    "used_memory": "1.5M",
                    "max_memory": "128M"
                },
                "rules": {
                    "default_rpm": 60,
                    "default_tpm": 100000,
                    "model_groups_count": 5
                }
            }
        }


class RateLimitErrorResponse(BaseModel):
    """Response schema for rate limit exceeded error (OpenAI compatible)."""
    
    error: Dict[str, Any] = Field(
        description="Error details in OpenAI format"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "error": {
                    "message": "Rate limit exceeded: 60/60 requests per minute. Please retry after 45 seconds.",
                    "type": "rate_limit_exceeded",
                    "param": None,
                    "code": "rate_limit_exceeded"
                }
            }
        }

