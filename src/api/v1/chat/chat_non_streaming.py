"""
Non-streaming chat completion handler.

This module provides non-streaming response handling with:
- Session expiry detection and automatic retry
- Proper error propagation using custom exceptions
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional, Tuple

import httpx
from fastapi.responses import JSONResponse

from ....services import proxy_router_service
from ....services import session_service
from ....db.database import get_db
from .chat_exceptions import (
    RequestParseError,
    SessionExpiredError,
    SessionCreationError,
    ProxyError,
    GatewayError,
)


def _parse_request(body: bytes) -> Tuple[list, dict]:
    """Parse request body into messages and chat params."""
    try:
        request_data = json.loads(body.decode("utf-8"))
        messages = request_data.get("messages", [])
        chat_params = {
            k: v for k, v in request_data.items() 
            if k not in ["messages", "stream", "session_id"]
        }
        return messages, chat_params
    except Exception as e:
        raise RequestParseError(message=f"Invalid JSON in request body: {e}") from e


def _parse_response(response: httpx.Response, logger, request_id: str) -> Tuple[Optional[dict], Optional[str]]:
    """Parse JSON response from proxy. Returns (content, error_message)."""
    try:
        return response.json(), None
    except Exception as e:
        logger.error(
            "Unexpected response format",
            request_id=request_id,
            error=str(e),
            response_text=response.text,
            status_code=response.status_code,
            event_type="unexpected_response_format",
        )
        return None, f"Unexpected response format from provider: '{response.text}'"


def _is_session_expired(error_content: str) -> bool:
    """Check if error indicates session expiration."""
    return "session expired" in error_content.lower()


def _make_error_response(status_code: int, message: str, error_type: str = "proxy_error", **extra) -> JSONResponse:
    """Create a standardized error JSONResponse."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": error_type, **extra}},
    )


async def _make_proxy_request(session_id: str, messages: list, chat_params: dict) -> httpx.Response:
    """Make a chat completion request to the proxy router."""
    return await proxy_router_service.chatCompletions(
        session_id=session_id,
        messages=messages,
        **chat_params,
    )


async def _create_new_session(
    db_api_key,
    user,
    requested_model: Optional[str],
    logger,
    request_id: str,
) -> Optional[str]:
    """Create a new session to replace an expired one."""
    logger.info(
        "Creating new session to replace expired session",
        user_id=user.id,
        api_key_id=db_api_key.id,
        requested_model=requested_model,
        event_type="new_session_creation_start",
    )
    
    try:
        async with get_db() as db:
            new_session = await session_service.get_session_for_api_key(
                db=db,
                api_key_id=db_api_key.id,
                user_id=user.id,
                requested_model=requested_model,
            )
            if not new_session:
                logger.error(
                    "Failed to create new session - automation may be disabled",
                    event_type="new_session_creation_failed",
                )
                return None
            
            logger.info(
                "Created new session successfully",
                new_session_id=new_session.id,
                event_type="new_session_created",
            )
            
            await asyncio.sleep(1.0)  # Brief delay to ensure session is registered
            return new_session.id
            
    except Exception as e:
        logger.error(
            "Failed to create new session",
            error=str(e),
            event_type="new_session_creation_failed",
        )
        return None


async def handle_non_streaming_request(
    *,
    logger,
    request_id: str,
    body: bytes,
    db_api_key,
    user,
    requested_model: Optional[str],
    session_id: str,
) -> JSONResponse:
    """
    Handle non-streaming proxy call with error parsing and retry on session expiry.
    
    Note: Uses short-lived DB connections for session creation to avoid
    holding connections during long-running operations.
    """
    # Parse request
    try:
        messages, chat_params = _parse_request(body)
    except RequestParseError as e:
        return e.to_response()

    # First attempt
    try:
        response = await _make_proxy_request(session_id, messages, chat_params)
    except proxy_router_service.ProxyRouterServiceError as e:
        logger.error(
            "Proxy router error on initial request",
            error=str(e),
            error_type=e.error_type,
            session_id=session_id,
            event_type="proxy_router_error",
        )
        return _make_error_response(e.get_http_status_code(), str(e), e.error_type)

    # Handle success
    if response.status_code == 200:
        content, error = _parse_response(response, logger, request_id)
        if content:
            logger.info(
                "Non-streaming chat completion successful",
                session_id=session_id,
                event_type="chat_completion_success",
            )
            return JSONResponse(content=content, status_code=200)
        return _make_error_response(500, error, "unexpected_response_format", session_id=session_id)

    # Handle error response
    error_content = response.text
    logger.error(
        "Proxy router error response",
        status_code=response.status_code,
        session_id=session_id,
        event_type="proxy_error_response",
    )

    # Check for session expired - attempt retry
    if _is_session_expired(error_content) and db_api_key and user:
        logger.warning(
            "Detected session expired error, will create new session and retry",
            session_id=session_id,
            event_type="session_expired_detected",
        )
        
        new_session_id = await _create_new_session(db_api_key, user, requested_model, logger, request_id)
        if new_session_id:
            return await _retry_with_new_session(
                new_session_id=new_session_id,
                original_session_id=session_id,
                messages=messages,
                chat_params=chat_params,
                logger=logger,
                request_id=request_id,
            )

    # Return original error
    try:
        error_json = json.loads(error_content)
        return JSONResponse(status_code=response.status_code, content=error_json)
    except json.JSONDecodeError:
        return _make_error_response(
            response.status_code,
            f"Proxy router error: {error_content}",
            status=response.status_code,
        )


async def _retry_with_new_session(
    new_session_id: str,
    original_session_id: str,
    messages: list,
    chat_params: dict,
    logger,
    request_id: str,
) -> JSONResponse:
    """Retry the request with a new session."""
    logger.info(
        "Retrying request with new session",
        new_session_id=new_session_id,
        original_session_id=original_session_id,
        event_type="session_retry_start",
    )

    try:
        response = await _make_proxy_request(new_session_id, messages, chat_params)
    except proxy_router_service.ProxyRouterServiceError as e:
        logger.error(
            "Retry request failed",
            new_session_id=new_session_id,
            error=str(e),
            error_type=e.error_type,
            event_type="session_retry_failed",
        )
        return _make_error_response(
            e.get_http_status_code(),
            f"Retry after session refresh failed: {e}",
            "retry_failed",
        )

    content, error = _parse_response(response, logger, request_id)
    if content:
        logger.info(
            "Non-streaming chat completion successful after retry",
            session_id=new_session_id,
            original_session_id=original_session_id,
            event_type="chat_completion_success",
        )
        return JSONResponse(content=content, status_code=200)
    
    return _make_error_response(500, error, "unexpected_response_format", session_id=new_session_id)
