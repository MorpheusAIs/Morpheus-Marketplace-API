"""
Rate Limiting FastAPI Dependencies

Provides FastAPI dependencies for rate limiting integration.
"""

from typing import Optional, Callable, Awaitable
from functools import wraps

from fastapi import Request, HTTPException, status, Depends
from fastapi.responses import JSONResponse

from src.core.config import settings
from src.core.logging_config import get_api_logger

from .types import RateLimitResult, RateLimitStatus
from .rate_limit_service import rate_limit_service

logger = get_api_logger()


class RateLimitExceeded(HTTPException):
    """
    Exception raised when rate limit is exceeded.
    
    Includes OpenAI-compatible error format and rate limit headers.
    """
    
    def __init__(
        self,
        result: RateLimitResult,
        detail: Optional[str] = None,
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


def get_user_identifier(request: Request) -> str:
    """
    Extract user identifier from request for rate limiting.
    
    Priority:
    1. User ID from request state (if authenticated)
    2. API key prefix from Authorization header
    3. Client IP address (fallback)
    
    Args:
        request: The FastAPI request
        
    Returns:
        User identifier string
    """
    # Try to get user ID from request state (set by auth dependency)
    if hasattr(request.state, "user") and request.state.user:
        return f"user:{request.state.user.id}"
    
    # Try to get API key prefix from Authorization header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer sk-") and len(auth_header) >= 16:
        # Extract API key prefix (sk- + 6 chars)
        api_key_prefix = auth_header[7:16]
        return f"key:{api_key_prefix}"
    
    # Fallback to client IP
    # Check for proxy headers first
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP in the chain (original client)
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"
    
    return f"ip:{client_ip}"


async def check_rate_limit(
    request: Request,
    model: Optional[str] = None,
    estimated_tokens: int = 0,
) -> RateLimitResult:
    """
    Check rate limits for a request.
    
    This is the main dependency for rate limit checking.
    
    Args:
        request: The FastAPI request
        model: The model being requested
        estimated_tokens: Estimated input tokens
        
    Returns:
        RateLimitResult
        
    Raises:
        RateLimitExceeded: If rate limit is exceeded
    """
    if not settings.RATE_LIMIT_ENABLED:
        return RateLimitResult(
            allowed=True,
            status=RateLimitStatus.ALLOWED,
        )
    
    user_id = get_user_identifier(request)
    
    # Generate a request ID for tracking
    request_id = getattr(request.state, "request_id", None)
    
    result = await rate_limit_service.check_rate_limit(
        user_id=user_id,
        model=model,
        estimated_tokens=estimated_tokens,
        request_id=request_id,
    )
    
    if not result.allowed and result.status != RateLimitStatus.ERROR:
        raise RateLimitExceeded(result)
    
    # Store result in request state for later header injection
    request.state.rate_limit_result = result
    
    return result


def rate_limit_dependency(
    model_param: str = "model",
    estimate_tokens: bool = False,
):
    """
    Create a rate limit dependency for an endpoint.
    
    Args:
        model_param: Name of the body parameter containing the model
        estimate_tokens: Whether to estimate tokens from request body
        
    Returns:
        FastAPI dependency
    """
    async def dependency(request: Request) -> RateLimitResult:
        model = None
        estimated_tokens = 0
        
        # Try to extract model from request body
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                # Read body without consuming it
                body = await request.json()
                model = body.get(model_param)
                
                if estimate_tokens and "messages" in body:
                    # Rough estimation: ~4 chars per token
                    messages = body.get("messages", [])
                    total_chars = sum(
                        len(str(m.get("content", ""))) 
                        for m in messages if isinstance(m, dict)
                    )
                    estimated_tokens = total_chars // 4
            except Exception:
                pass
        
        return await check_rate_limit(request, model, estimated_tokens)
    
    return dependency


def add_rate_limit_headers(response: JSONResponse, result: RateLimitResult) -> JSONResponse:
    """
    Add rate limit headers to a response.
    
    Args:
        response: The response to modify
        result: Rate limit result
        
    Returns:
        Modified response with headers
    """
    if result and result.rpm_limit > 0:
        headers = rate_limit_service.create_rate_limit_headers(result)
        for key, value in headers.to_dict().items():
            response.headers[key] = value
    
    return response

