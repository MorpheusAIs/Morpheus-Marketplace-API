"""
Rate Limiting FastAPI Dependencies

Provides FastAPI dependencies for rate limiting integration.
"""

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

from .types import RateLimitResult, RateLimitStatus
from .rate_limit_service import rate_limit_service


class RateLimitExceeded(HTTPException):
    """
    Exception raised when rate limit is exceeded.
    
    Includes OpenAI-compatible error format and rate limit headers.
    """
    
    def __init__(
        self,
        result: RateLimitResult,
        detail: str | None = None,
    ):
        self.result = result
        
        if result.status == RateLimitStatus.EXCEEDED_RPM:
            message = (
                f"Rate limit exceeded: {result.rpm_current}/{result.rpm_limit} "
                f"requests per minute. Please retry after {result.retry_after} seconds."
            )
        elif result.status == RateLimitStatus.EXCEEDED_TPM:
            message = (
                f"Rate limit exceeded: {result.tpm_current}/{result.tpm_limit} "
                f"tokens per minute. Please retry after {result.retry_after} seconds."
            )
        else:
            message = detail or "Rate limit exceeded"
        
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=message,
        )
    
    def to_openai_response(self) -> JSONResponse:
        """Create an OpenAI-compatible error response."""
        headers = rate_limit_service.create_rate_limit_headers(self.result)
        
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "error": {
                    "message": self.detail,
                    "type": "rate_limit_exceeded",
                    "param": None,
                    "code": "rate_limit_exceeded",
                }
            },
            headers=headers.to_dict(),
        )
