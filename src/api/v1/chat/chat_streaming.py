from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator, Callable, Dict, Optional

from ....services import proxy_router_service
from ....services import session_service


def build_stream_generator(
    *,
    logger,
    session_id: str,
    body: bytes,
    requested_model: Optional[str],
    db_api_key,
    user,
    db,
) -> Callable[[], AsyncIterator[bytes]]:
    """Return a zero-arg async generator function that streams proxy responses.

    The emitted log messages and error texts are preserved 1:1 with the original
    implementation to ensure behavior parity.
    """

    async def stream_generator() -> AsyncIterator[bytes]:
        stream_trace_id = str(uuid.uuid4())[:8]
        stream_logger = logger.bind(stream_trace_id=stream_trace_id, session_id=session_id)
        
        stream_logger.info("Starting stream generator",
                          session_id=session_id,
                          user_id=user.id if user else None,
                          requested_model=requested_model,
                          event_type="stream_generator_start")
        chunk_count = 0
        req_body_json: Optional[Dict[str, Any]] = None

        try:
            # Parse the request body for debugging - do this before the request
            try:
                req_body_json = json.loads(body.decode("utf-8"))
                messages = req_body_json.get('messages', [])
                # Extract other parameters (tools, model, etc.)
                chat_params = {k: v for k, v in req_body_json.items() if k not in ['messages', 'stream', 'session_id']}
                
                has_tool_msg = any(
                    msg.get("role") == "tool"
                    for msg in messages
                    if isinstance(msg, dict)
                )
                has_tool_calls = any(
                    "tool_calls" in msg
                    for msg in messages
                    if isinstance(msg, dict)
                )

                if has_tool_msg or has_tool_calls:
                    stream_logger.info("Request contains tool content",
                                      has_tool_messages=has_tool_msg,
                                      has_tool_calls=has_tool_calls,
                                      message_count=len(messages),
                                      event_type="tool_content_detected")
            except Exception as parse_err:  # noqa: BLE001 - behavior parity
                stream_logger.error("Failed to parse request body",
                                   error=str(parse_err),
                                   event_type="stream_body_parse_error")
                # Fallback to empty values if parsing fails
                messages = []
                chat_params = {}
                req_body_json = {}

            # First attempt with existing session
            stream_logger.info("Making streaming request",
                              session_id=session_id,
                              event_type="stream_request_start")

            # Track if we need to retry due to expired session
            retry_with_new_session = False
            new_session_id: Optional[str] = None

            try:
                async with proxy_router_service.chatCompletionsStream(
                    session_id=session_id,
                    messages=messages,
                    **chat_params
                ) as response:
                    # Log proxy status
                    stream_logger.info("Proxy router response received",
                                      status_code=response.status_code,
                                      response_headers=dict(response.headers.items()),
                                      event_type="stream_proxy_response")

                    if response.status_code != 200:
                        stream_logger.error("Proxy router error response",
                                           status_code=response.status_code,
                                           session_id=session_id,
                                           event_type="stream_proxy_error")
                        # Try to read and log the error body
                        try:
                            error_body = await response.aread()
                            error_text = error_body.decode("utf-8", errors="replace")
                            stream_logger.error("Error body received from proxy",
                                               error_text=error_text,
                                               status_code=response.status_code,
                                               event_type="stream_proxy_error_body")

                            # Check if this is a session expired error
                            if "session expired" in error_text.lower():
                                stream_logger.warning("Detected session expired error, will create new session and retry",
                                                      session_id=session_id,
                                                      event_type="stream_session_expired_detected")
                                retry_with_new_session = True

                                if db_api_key and user:
                                    try:
                                        stream_logger.info("Creating new session to replace expired session",
                                                          user_id=user.id,
                                                          api_key_id=db_api_key.id,
                                                          requested_model=requested_model,
                                                          event_type="stream_new_session_creation_start")
                                        new_session = await session_service.create_automated_session(
                                            db=db,
                                            api_key_id=db_api_key.id,
                                            user_id=user.id,
                                            requested_model=requested_model,
                                        )
                                        new_session_id = new_session.id
                                        stream_logger.info("Created new session for stream retry",
                                                          new_session_id=new_session_id,
                                                          event_type="stream_new_session_created")

                                        # Add a small delay to ensure the session is fully registered
                                        stream_logger.debug("Adding brief delay to ensure session is registered")
                                        await asyncio.sleep(1.0)
                                    except Exception as e:  # noqa: BLE001
                                        stream_logger.error("Failed to create new session",
                                                           error=str(e),
                                                           event_type="stream_new_session_creation_failed")
                                        retry_with_new_session = False

                            # If not retrying, return error to client
                            if not retry_with_new_session:
                                # Return a formatted error message to the client
                                error_msg = {
                                    "error": {
                                        "message": f"Proxy router error: {error_text}",
                                        "type": "proxy_error",
                                        "status": response.status_code,
                                    }
                                }
                                yield f"data: {json.dumps(error_msg)}\n\n".encode("utf-8")
                                return
                        except Exception as read_err:  # noqa: BLE001
                            stream_logger.error("Error reading error response",
                                               error=str(read_err),
                                               event_type="stream_error_read_failed")
                            retry_with_new_session = False

                    # If not retrying, process the response normally
                    if not retry_with_new_session:
                        # Simple byte streaming
                        async for chunk_bytes in response.aiter_bytes():
                            chunk_count += 1
                            # For debugging, log first few chunks
                            if chunk_count <= 2:
                                try:
                                    preview = chunk_bytes[:150].decode("utf-8", errors="replace")
                                    stream_logger.debug("Stream chunk preview",
                                                       chunk_number=chunk_count,
                                                       preview=preview,
                                                       chunk_size=len(chunk_bytes))
                                except Exception:  # noqa: BLE001
                                    stream_logger.debug("Stream chunk received (binary data)",
                                                       chunk_number=chunk_count,
                                                       chunk_size=len(chunk_bytes))
                            yield chunk_bytes

                        stream_logger.info("Stream finished from proxy",
                                          total_chunks=chunk_count,
                                          session_id=session_id,
                                          event_type="stream_completed")

            except proxy_router_service.ProxyRouterServiceError as e:
                stream_logger.error("Proxy router error during streaming",
                                   error=str(e),
                                   error_type=e.error_type,
                                   status_code=e.status_code,
                                   event_type="stream_proxy_router_error")
                error_msg = {
                    "error": {
                        "message": str(e),
                        "type": e.error_type,
                        "status": e.status_code,
                    }
                }
                yield f"data: {json.dumps(error_msg)}\n\n".encode("utf-8")
                return

            # If we need to retry with a new session, do that now
            if retry_with_new_session and new_session_id:
                stream_logger.info("Retrying stream request with new session",
                                  new_session_id=new_session_id,
                                  original_session_id=session_id,
                                  event_type="stream_retry_start")

                # Make the retry request
                try:
                    async with proxy_router_service.chatCompletionsStream(
                        session_id=new_session_id,
                        messages=messages,
                        **chat_params
                    ) as retry_response:
                        stream_logger.info("Retry request response received",
                                          status_code=retry_response.status_code,
                                          new_session_id=new_session_id,
                                          event_type="stream_retry_response")

                        if retry_response.status_code != 200:
                            stream_logger.error("Retry request failed",
                                               status_code=retry_response.status_code,
                                               new_session_id=new_session_id,
                                               event_type="stream_retry_failed")
                            error_body = await retry_response.aread()
                            error_text = error_body.decode("utf-8", errors="replace")
                            error_msg = {
                                "error": {
                                    "message": f"Retry after session refresh failed: {error_text}",
                                    "type": "retry_failed",
                                    "status": retry_response.status_code,
                                }
                            }
                            yield f"data: {json.dumps(error_msg)}\n\n".encode("utf-8")
                            return

                        # Stream the retry response
                        retry_chunk_count = 0
                        async for chunk_bytes in retry_response.aiter_bytes():
                            retry_chunk_count += 1
                            if retry_chunk_count <= 2:
                                try:
                                    preview = chunk_bytes[:150].decode("utf-8", errors="replace")
                                    stream_logger.debug("Retry stream chunk preview",
                                                       chunk_number=retry_chunk_count,
                                                       preview=preview,
                                                       chunk_size=len(chunk_bytes))
                                except Exception:  # noqa: BLE001
                                    stream_logger.debug("Retry stream chunk received (binary data)",
                                                       chunk_number=retry_chunk_count,
                                                       chunk_size=len(chunk_bytes))
                            yield chunk_bytes

                        stream_logger.info("Retry stream finished successfully",
                                          total_chunks=retry_chunk_count,
                                          new_session_id=new_session_id,
                                          event_type="stream_retry_completed")
                        
                except proxy_router_service.ProxyRouterServiceError as e:
                    stream_logger.error("Retry request failed with proxy router error",
                                       error=str(e),
                                       error_type=e.error_type,
                                       new_session_id=new_session_id,
                                       event_type="stream_retry_proxy_error")
                    error_msg = {
                        "error": {
                            "message": f"Retry after session refresh failed: {str(e)}",
                            "type": "retry_failed",
                        }
                    }
                    yield f"data: {json.dumps(error_msg)}\n\n".encode("utf-8")
                    return

        except Exception as e:  # noqa: BLE001
            stream_logger.error("Error in stream_generator",
                               error=str(e),
                               session_id=session_id,
                               chunk_count=chunk_count,
                               event_type="stream_generator_error",
                               exc_info=True)
            # Yield a generic error message as bytes
            error_msg = {
                "error": {
                    "message": f"Error in API gateway streaming: {str(e)}",
                    "type": "gateway_error",
                    "session_id": session_id,
                }
            }
            error_message = f"data: {json.dumps(error_msg)}\n\n"
            yield error_message.encode("utf-8")

    return stream_generator


__all__ = ["build_stream_generator"]