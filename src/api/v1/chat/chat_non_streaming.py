from __future__ import annotations

import asyncio
import json
from typing import Dict, Optional, Tuple

import httpx
from fastapi.responses import JSONResponse

from ....services import proxy_router_service
from ....services import session_service

def safe_parse_json_response(response: httpx.Response, logger, request_id: str, messages: list, chat_params: dict) -> Tuple[dict, Exception]:
    """Safely parse a JSON response from the proxy router."""
    try:
        return response.json(), None
    except Exception as e:
        payload = {
            "messages": messages,
            "stream": False,
            **chat_params
        }
        
        # Provide more specific error message for empty responses
        error_msg = str(e)
        if not response.text or response.text.strip() == "":
            error_msg = "Empty response body from model provider"
            logger.error("Model provider returned empty response",
                        request_id=request_id,
                        error=str(e),
                        response_status_code=response.status_code,
                        content_length=len(response.text) if response.text else 0,
                        payload=payload,
                        event_type="empty_model_response")
        else:
            logger.error("Unexpected response format from model provider",
                        request_id=request_id,
                        error=str(e),
                        response_text=response.text[:500],  # Truncate to avoid huge logs
                        response_status_code=response.status_code,
                        payload=payload,
                        event_type="unexpected_response_format")
        
        return None, Exception(f"Invalid response from model provider: {error_msg}")

async def handle_non_streaming_request(
    *,
    logger,
    request_id: str,
    body: bytes,
    db_api_key,
    user,
    requested_model: Optional[str],
    db,
    session_id: str,
):
    """Perform the non-streaming proxy call with error parsing and retry semantics.

    This mirrors chat.py's original behavior and logging exactly.
    Returns a JSONResponse.
    """

    # Parse the request body to get messages and other parameters
    try:
        request_data = json.loads(body.decode('utf-8'))
        messages = request_data.get('messages', [])
        # Extract other parameters (tools, model, etc.)
        chat_params = {k: v for k, v in request_data.items() if k not in ['messages', 'stream', 'session_id']}
    except Exception as e:
        logger.error("Failed to parse request body",
                    request_id=request_id,
                    error=str(e),
                    event_type="request_parse_error")
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": f"Invalid JSON in request body: {str(e)}",
                    "type": "invalid_request",
                }
            }
        )

    # First attempt with original session
    try:
        response = await proxy_router_service.chatCompletions(
            session_id=session_id,
            messages=messages,
            **chat_params
        )
    except proxy_router_service.ProxyRouterServiceError as e:
        logger.error("Proxy router error on initial request",
                    request_id=request_id,
                    error=str(e),
                    error_type=e.error_type,
                    session_id=session_id,
                    event_type="proxy_router_error")
        return JSONResponse(
            status_code=e.get_http_status_code(),
            content={
                "error": {
                    "message": str(e),
                    "type": e.error_type,
                }
            }
        )

    # Check if this is a session expired error
    retry_with_new_session = False
    new_session_id: Optional[str] = None

    # Check response status
    if response.status_code != 200:
        logger.error("Proxy router error response",
                    request_id=request_id,
                    status_code=response.status_code,
                    session_id=session_id,
                    event_type="proxy_error_response")
        try:
            error_content = response.text

            # Check if this is a session expired error
            if "session expired" in error_content.lower():
                logger.warning("Detected session expired error, will create new session and retry",
                              request_id=request_id,
                              session_id=session_id,
                              event_type="session_expired_detected")
                retry_with_new_session = True

                if db_api_key and user:
                    try:
                        logger.info("Creating new session to replace expired session",
                                   request_id=request_id,
                                   user_id=user.id,
                                   api_key_id=db_api_key.id,
                                   requested_model=requested_model,
                                   event_type="new_session_creation_start")
                        new_session = await session_service.create_automated_session(
                            db=db,
                            api_key_id=db_api_key.id,
                            user_id=user.id,
                            requested_model=requested_model,
                        )
                        new_session_id = new_session.id
                        logger.info("Created new session successfully",
                                   request_id=request_id,
                                   new_session_id=new_session_id,
                                   event_type="new_session_created")

                        # Add a small delay to ensure the session is fully registered
                        logger.debug("Adding brief delay to ensure session is registered",
                                    request_id=request_id)
                        await asyncio.sleep(1.0)
                    except Exception as e:  # noqa: BLE001
                        logger.error("Failed to create new session",
                                    request_id=request_id,
                                    error=str(e),
                                    event_type="new_session_creation_failed")
                        retry_with_new_session = False

            # If not retrying, return error to client
            if not retry_with_new_session:
                try:
                    error_json = json.loads(error_content)
                    return JSONResponse(
                        status_code=response.status_code, content=error_json
                    )
                except Exception:
                    return JSONResponse(
                        status_code=response.status_code,
                        content={
                            "error": {
                                "message": f"Proxy router error: {error_content}",
                                "type": "proxy_error",
                                "status": response.status_code,
                            }
                        },
                    )
        except Exception as e:  # noqa: BLE001
            logger.error("Error parsing error response",
                        request_id=request_id,
                        error=str(e),
                        session_id=session_id,
                        event_type="error_response_parse_failed")
            retry_with_new_session = False
            return JSONResponse(
                status_code=response.status_code,
                content={
                    "error": {
                        "message": f"Proxy router error: {response.text}",
                        "type": "proxy_error",
                        "status": response.status_code,
                    }
                },
            )

    # If not retrying, return the original response
    if not retry_with_new_session:
        # Parse response BEFORE logging success
        response_content, parse_error = safe_parse_json_response(response, logger, request_id, messages, chat_params)
        
        if response_content:
            logger.info("Non-streaming chat completion successful",
                       request_id=request_id,
                       session_id=session_id,
                       event_type="chat_completion_success")
            return JSONResponse(content=response_content, status_code=200)
        else:
            logger.error("Non-streaming chat completion failed - invalid response format",
                        request_id=request_id,
                        session_id=session_id,
                        error=str(parse_error),
                        event_type="chat_completion_failed")
            return JSONResponse(status_code=502, content={
                "error": {
                    "message": "The AI model returned an invalid response. This may be due to a model timeout or failure.",
                    "type": "bad_gateway",
                    "session_id": session_id,
                    "details": str(parse_error)
                }
            })

    # If we need to retry with a new session, do that now
    if retry_with_new_session and new_session_id:
        logger.info("Retrying request with new session",
                   request_id=request_id,
                   new_session_id=new_session_id,
                   original_session_id=session_id,
                   event_type="session_retry_start")

        # Make the retry request with new session
        try:
            retry_response = await proxy_router_service.chatCompletions(
                session_id=new_session_id,
                messages=messages,
                **chat_params
            )
        except proxy_router_service.ProxyRouterServiceError as e:
            logger.error("Retry request failed",
                        request_id=request_id,
                        new_session_id=new_session_id,
                        error=str(e),
                        error_type=e.error_type,
                        event_type="session_retry_failed")
            return JSONResponse(
                status_code=e.get_http_status_code(),
                content={
                    "error": {
                        "message": f"Retry after session refresh failed: {str(e)}",
                        "type": "retry_failed",
                    }
                }
            )

        # Parse retry response BEFORE logging success
        retry_content, parse_error = safe_parse_json_response(retry_response, logger, request_id, messages, chat_params)
        
        if retry_content:
            logger.info("Non-streaming chat completion successful after retry",
                       request_id=request_id,
                       session_id=new_session_id,
                       original_session_id=session_id,
                       event_type="chat_completion_success")
            return JSONResponse(content=retry_content, status_code=200)
        else:
            logger.error("Non-streaming chat completion failed after retry - invalid response format",
                        request_id=request_id,
                        session_id=new_session_id,
                        original_session_id=session_id,
                        error=str(parse_error),
                        event_type="chat_completion_retry_failed")
            return JSONResponse(status_code=502, content={
                "error": {
                    "message": "The AI model returned an invalid response after retry. This may be due to a model timeout or failure.",
                    "type": "bad_gateway",
                    "session_id": new_session_id,
                    "details": str(parse_error)
                }
            })


