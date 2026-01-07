"""
Custom exceptions for the chat module.

Following FastAPI best practices for exception handling:
- Specific exception classes for different error scenarios
- Consistent error response format (OpenAI-compatible)
- Clear separation between client and server errors
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import status
from fastapi.responses import JSONResponse


@dataclass
class ChatError(Exception):
    """Base exception for all chat-related errors."""
    
    message: str
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_type: str = "chat_error"
    details: dict = field(default_factory=dict)
    
    def __post_init__(self):
        super().__init__(self.message)
    
    def to_response(self) -> JSONResponse:
        """Convert exception to a JSONResponse."""
        content = {
            "error": {
                "message": self.message,
                "type": self.error_type,
                **self.details,
            }
        }
        return JSONResponse(status_code=self.status_code, content=content)


@dataclass
class InsufficientBalanceError(ChatError):
    """Raised when user has insufficient credits for the request."""
    
    available_balance: Optional[str] = None
    estimated_cost: Optional[str] = None
    message: str = "Insufficient credits"
    status_code: int = status.HTTP_402_PAYMENT_REQUIRED
    error_type: str = "insufficient_balance"
    
    def __post_init__(self):
        self.details = {
            "available": self.available_balance,
            "estimated_cost": self.estimated_cost,
        }
        super().__post_init__()


@dataclass
class BillingError(ChatError):
    """Raised when billing operations fail."""
    
    message: str = "Billing error occurred"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_type: str = "billing_error"


@dataclass
class SessionError(ChatError):
    """Base class for session-related errors."""
    
    session_id: Optional[str] = None
    message: str = "Session error"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_type: str = "session_error"
    
    def __post_init__(self):
        if self.session_id:
            self.details = {"session_id": self.session_id}
        super().__post_init__()


@dataclass  
class SessionNotFoundError(SessionError):
    """Raised when no valid session is available."""
    
    message: str = "No session ID provided in request and no active session found for API key"
    status_code: int = status.HTTP_400_BAD_REQUEST
    error_type: str = "session_not_found"


@dataclass
class SessionExpiredError(SessionError):
    """Raised when a session has expired and needs refresh."""
    
    message: str = "Session expired"
    status_code: int = status.HTTP_400_BAD_REQUEST
    error_type: str = "session_expired"


@dataclass
class SessionCreationError(SessionError):
    """Raised when session creation fails."""
    
    message: str = "Failed to create session"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_type: str = "session_creation_error"


@dataclass
class ProxyError(ChatError):
    """Raised when proxy router communication fails."""
    
    proxy_status: Optional[int] = None
    message: str = "Proxy router error"
    status_code: int = status.HTTP_502_BAD_GATEWAY
    error_type: str = "proxy_error"
    
    def __post_init__(self):
        if self.proxy_status:
            self.details = {"status": self.proxy_status}
        super().__post_init__()


@dataclass
class GatewayError(ChatError):
    """Raised for general API gateway errors."""
    
    session_id: Optional[str] = None
    message: str = "Error in API gateway"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_type: str = "gateway_error"
    
    def __post_init__(self):
        if self.session_id:
            self.details = {"session_id": self.session_id}
        super().__post_init__()


@dataclass
class RequestParseError(ChatError):
    """Raised when request body parsing fails."""
    
    message: str = "Invalid request format"
    status_code: int = status.HTTP_400_BAD_REQUEST
    error_type: str = "invalid_request"


def handle_chat_error(error: ChatError, logger, request_id: str) -> JSONResponse:
    """
    Handle a ChatError by logging and converting to response.
    
    Args:
        error: The ChatError instance
        logger: Bound logger for context
        request_id: Request ID for tracing
        
    Returns:
        JSONResponse with appropriate status and error details
    """
    logger.error(
        error.message,
        request_id=request_id,
        error_type=error.error_type,
        status_code=error.status_code,
        event_type=f"chat_error_{error.error_type}",
        **error.details,
    )
    return error.to_response()


__all__ = [
    "ChatError",
    "InsufficientBalanceError", 
    "BillingError",
    "SessionError",
    "SessionNotFoundError",
    "SessionExpiredError",
    "SessionCreationError",
    "ProxyError",
    "GatewayError",
    "RequestParseError",
    "handle_chat_error",
]

