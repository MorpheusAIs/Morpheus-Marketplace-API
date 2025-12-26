"""
Streaming chat completion handler with billing integration.

This module provides streaming response handling with:
- Billing holds created before streaming starts (in index.py)
- Token usage extraction from final SSE chunks
- Automatic finalization or voiding based on stream outcome
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Optional, TYPE_CHECKING

from ....services import proxy_router_service
from ....services import session_service
from ....services.billing_service import billing_service
from ....schemas.billing import UsageFinalizeRequest, UsageVoidRequest
from ....db.database import get_db
from .chat_exceptions import (
    SessionExpiredError,
    SessionCreationError,
    ProxyError,
    GatewayError,
)

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger


@dataclass
class StreamingBillingParams:
    """Parameters for streaming billing integration."""
    
    user_id: int
    api_key_id: int
    model_name: Optional[str]
    model_id: Optional[str]
    estimated_input_tokens: int
    estimated_output_tokens: int


@dataclass 
class StreamingUsageAccumulator:
    """Accumulates usage data during streaming for finalization."""
    
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_total: int = 0
    model_name: Optional[str] = None
    model_id: Optional[str] = None
    endpoint: str = "/v1/chat/completions"
    
    def update_from_usage(self, usage: dict) -> None:
        """Update accumulated tokens from provider usage dict."""
        self.tokens_input = usage.get("prompt_tokens", self.tokens_input)
        self.tokens_output = usage.get("completion_tokens", self.tokens_output)
        self.tokens_total = usage.get("total_tokens", self.tokens_input + self.tokens_output)


@dataclass
class StreamResult:
    """Result metadata from stream processing (not yielded to client)."""
    
    success: bool
    chunk_count: int = 0
    error: Optional[str] = None
    needs_retry: bool = False


def parse_sse_usage(chunk_bytes: bytes) -> Optional[Dict[str, Any]]:
    """
    Parse SSE chunk to extract usage_from_provider data.
    
    Provider typically sends usage in the final chunk with format:
        data: {"usage_from_provider": {"prompt_tokens": X, "completion_tokens": Y, "total_tokens": Z}}
    
    Returns the usage dict if found, None otherwise.
    """
    try:
        chunk_text = chunk_bytes.decode("utf-8", errors="replace")
        
        for line in chunk_text.split("\n"):
            line = line.strip()
            if not line.startswith("data:"):
                continue
            
            json_str = line[5:].strip()
            if not json_str or json_str == "[DONE]":
                continue
            
            data = json.loads(json_str)
            if "usage_from_provider" in data:
                return data["usage_from_provider"]
                
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    
    return None


def _format_sse_error(error_type: str, message: str, **extra) -> bytes:
    """Format an error as an SSE data chunk."""
    error_msg = {"error": {"message": message, "type": error_type, **extra}}
    return f"data: {json.dumps(error_msg)}\n\n".encode("utf-8")


async def _finalize_streaming_billing(
    user_id: int,
    ledger_entry_id: uuid.UUID,
    accumulator: StreamingUsageAccumulator,
    logger: "BoundLogger",
) -> None:
    """Finalize billing after successful stream completion."""
    try:
        async with get_db() as db:
            finalize_request = UsageFinalizeRequest(
                ledger_entry_id=ledger_entry_id,
                tokens_input=accumulator.tokens_input,
                tokens_output=accumulator.tokens_output,
                tokens_total=accumulator.tokens_total,
                model_name=accumulator.model_name,
                model_id=accumulator.model_id,
                endpoint=accumulator.endpoint,
            )
            response = await billing_service.finalize_usage(db, user_id, finalize_request)
            
            logger.info(
                "Streaming billing finalized",
                tokens_input=accumulator.tokens_input,
                tokens_output=accumulator.tokens_output,
                amount_total=str(response.amount_total),
                event_type="streaming_billing_finalized",
            )
    except Exception as e:
        logger.error(
            "Failed to finalize streaming billing",
            error=str(e),
            event_type="streaming_billing_finalize_error",
            exc_info=True,
        )


async def _void_streaming_billing(
    user_id: int,
    ledger_entry_id: uuid.UUID,
    failure_code: str,
    failure_reason: str,
    logger: "BoundLogger",
) -> None:
    """Void billing hold on stream failure."""
    try:
        async with get_db() as db:
            void_request = UsageVoidRequest(
                ledger_entry_id=ledger_entry_id,
                failure_code=failure_code,
                failure_reason=failure_reason,
            )
            await billing_service.void_usage(db, user_id, void_request)
            
            logger.info(
                "Streaming billing hold voided",
                failure_code=failure_code,
                event_type="streaming_billing_voided",
            )
    except Exception as e:
        logger.error(
            "Failed to void streaming billing hold",
            error=str(e),
            event_type="streaming_billing_void_error",
            exc_info=True,
        )


def build_stream_generator(
    *,
    logger: "BoundLogger",
    session_id: str,
    body: bytes,
    requested_model: Optional[str],
    db_api_key,
    user,
    ledger_entry_id: Optional[uuid.UUID] = None,
    billing_params: Optional[StreamingBillingParams] = None,
) -> Callable[[], AsyncIterator[bytes]]:
    """
    Return a zero-arg async generator function that streams proxy responses.

    Args:
        logger: Bound logger instance
        session_id: Session ID for the proxy request
        body: Request body bytes
        requested_model: Model name requested
        db_api_key: API key object
        user: User object
        ledger_entry_id: If provided, billing is enabled for this stream
        billing_params: Billing parameters (required if ledger_entry_id is set)
    
    Note: Uses short-lived DB connections for session creation to avoid
    holding connections during long-running streaming operations.
    """
    billing_enabled = ledger_entry_id is not None and billing_params is not None

    async def stream_generator() -> AsyncIterator[bytes]:
        stream_trace_id = str(uuid.uuid4())[:8]
        stream_logger = logger.bind(
            stream_trace_id=stream_trace_id,
            session_id=session_id,
            billing_enabled=billing_enabled,
        )
        
        stream_logger.info(
            "Starting stream generator",
            user_id=user.id if user else None,
            requested_model=requested_model,
            event_type="stream_generator_start",
        )
        
        chunk_count = 0
        stream_completed_successfully = False
        stream_error: Optional[str] = None
        
        accumulator = StreamingUsageAccumulator(
            model_name=requested_model,
            model_id=billing_params.model_id if billing_params else None,
        ) if billing_enabled else None

        messages, chat_params = _parse_request_body(body, stream_logger)

        try:
            async for chunk_data in _process_stream_request(
                session_id=session_id,
                messages=messages,
                chat_params=chat_params,
                logger=stream_logger,
                accumulator=accumulator,
            ):
                if isinstance(chunk_data, StreamResult):
                    chunk_count = chunk_data.chunk_count
                    stream_completed_successfully = chunk_data.success
                    stream_error = chunk_data.error
                    
                    if chunk_data.needs_retry and db_api_key and user:
                        async for retry_chunk in _handle_session_retry(
                            original_session_id=session_id,
                            messages=messages,
                            chat_params=chat_params,
                            db_api_key=db_api_key,
                            user=user,
                            requested_model=requested_model,
                            logger=stream_logger,
                            accumulator=accumulator,
                        ):
                            if isinstance(retry_chunk, StreamResult):
                                chunk_count = retry_chunk.chunk_count
                                stream_completed_successfully = retry_chunk.success
                                stream_error = retry_chunk.error
                            else:
                                yield retry_chunk
                else:
                    yield chunk_data
                    
        except Exception as e:
            stream_error = str(e)
            stream_logger.error(
                "Error in stream_generator",
                error=str(e),
                chunk_count=chunk_count,
                event_type="stream_generator_error",
                exc_info=True,
            )
            yield _format_sse_error("gateway_error", f"Error in API gateway streaming: {e}", session_id=session_id)
        
        finally:
            if billing_enabled and ledger_entry_id and billing_params:
                if stream_completed_successfully and accumulator:
                    await _finalize_streaming_billing(
                        user_id=billing_params.user_id,
                        ledger_entry_id=ledger_entry_id,
                        accumulator=accumulator,
                        logger=stream_logger,
                    )
                else:
                    await _void_streaming_billing(
                        user_id=billing_params.user_id,
                        ledger_entry_id=ledger_entry_id,
                        failure_code="stream_error" if stream_error else "stream_incomplete",
                        failure_reason=stream_error or "Stream did not complete successfully",
                        logger=stream_logger,
                    )

    return stream_generator


def _parse_request_body(body: bytes, logger: "BoundLogger") -> tuple[list, dict]:
    """Parse request body for messages and chat params."""
    try:
        req_body_json = json.loads(body.decode("utf-8"))
        messages = req_body_json.get("messages", [])
        chat_params = {
            k: v for k, v in req_body_json.items()
            if k not in ["messages", "stream", "session_id"]
        }
        
        has_tool_msg = any(msg.get("role") == "tool" for msg in messages if isinstance(msg, dict))
        has_tool_calls = any("tool_calls" in msg for msg in messages if isinstance(msg, dict))
        
        if has_tool_msg or has_tool_calls:
            logger.info(
                "Request contains tool content",
                has_tool_messages=has_tool_msg,
                has_tool_calls=has_tool_calls,
                message_count=len(messages),
                event_type="tool_content_detected",
            )
        
        return messages, chat_params
        
    except Exception as parse_err:
        logger.error(
            "Failed to parse request body",
            error=str(parse_err),
            event_type="stream_body_parse_error",
        )
        return [], {}


async def _process_stream_request(
    session_id: str,
    messages: list,
    chat_params: dict,
    logger: "BoundLogger",
    accumulator: Optional[StreamingUsageAccumulator] = None,
) -> AsyncIterator[bytes | StreamResult]:
    """
    Process a single streaming request attempt.
    
    Yields:
        - bytes: Chunk data to send to client
        - StreamResult: Final result metadata (not sent to client)
    """
    logger.info("Making streaming request", session_id=session_id, event_type="stream_request_start")
    chunk_count = 0
    
    try:
        async with proxy_router_service.chatCompletionsStream(
            session_id=session_id,
            messages=messages,
            **chat_params,
        ) as response:
            logger.info(
                "Proxy router response received",
                status_code=response.status_code,
                response_headers=dict(response.headers.items()),
                event_type="stream_proxy_response",
            )
            
            if response.status_code != 200:
                result = await _handle_error_response(response, session_id, logger)
                if result.needs_retry:
                    yield result
                    return
                if result.error:
                    yield _format_sse_error("proxy_error", f"Proxy router error: {result.error}", status=response.status_code)
                yield result
                return
            
            async for chunk_bytes in response.aiter_bytes():
                chunk_count += 1
                
                if accumulator and b"usage_from_provider" in chunk_bytes:
                    usage = parse_sse_usage(chunk_bytes)
                    if usage:
                        accumulator.update_from_usage(usage)
                        logger.debug(
                            "Extracted usage from stream",
                            tokens_input=accumulator.tokens_input,
                            tokens_output=accumulator.tokens_output,
                            event_type="stream_usage_extracted",
                        )
                
                if chunk_count <= 2:
                    try:
                        preview = chunk_bytes[:150].decode("utf-8", errors="replace")
                        logger.debug(
                            "Stream chunk preview",
                            chunk_number=chunk_count,
                            preview=preview,
                            chunk_size=len(chunk_bytes),
                        )
                    except Exception:
                        logger.debug(
                            "Stream chunk received (binary data)",
                            chunk_number=chunk_count,
                            chunk_size=len(chunk_bytes),
                        )
                
                yield chunk_bytes
            
            logger.info(
                "Stream finished from proxy",
                total_chunks=chunk_count,
                session_id=session_id,
                event_type="stream_completed",
            )
            yield StreamResult(success=True, chunk_count=chunk_count)
            
    except proxy_router_service.ProxyRouterServiceError as e:
        logger.error(
            "Proxy router error during streaming",
            error=str(e),
            error_type=e.error_type,
            status_code=e.status_code,
            event_type="stream_proxy_router_error",
        )
        yield _format_sse_error(e.error_type, str(e), status=e.status_code)
        yield StreamResult(success=False, chunk_count=chunk_count, error=str(e))


async def _handle_error_response(response, session_id: str, logger: "BoundLogger") -> StreamResult:
    """Handle non-200 response from proxy."""
    logger.error(
        "Proxy router error response",
        status_code=response.status_code,
        session_id=session_id,
        event_type="stream_proxy_error",
    )
    
    try:
        error_body = await response.aread()
        error_text = error_body.decode("utf-8", errors="replace")
        
        logger.error(
            "Error body received from proxy",
            error_text=error_text,
            status_code=response.status_code,
            event_type="stream_proxy_error_body",
        )
        
        if "session expired" in error_text.lower():
            logger.warning(
                "Detected session expired error, will create new session and retry",
                session_id=session_id,
                event_type="stream_session_expired_detected",
            )
            return StreamResult(success=False, error=error_text, needs_retry=True)
        
        return StreamResult(success=False, error=error_text)
        
    except Exception as read_err:
        logger.error(
            "Error reading error response",
            error=str(read_err),
            event_type="stream_error_read_failed",
        )
        return StreamResult(success=False, error=str(read_err))


async def _handle_session_retry(
    original_session_id: str,
    messages: list,
    chat_params: dict,
    db_api_key,
    user,
    requested_model: Optional[str],
    logger: "BoundLogger",
    accumulator: Optional[StreamingUsageAccumulator] = None,
) -> AsyncIterator[bytes | StreamResult]:
    """Handle session expiry by creating new session and retrying."""
    logger.info(
        "Creating new session to replace expired session",
        user_id=user.id,
        api_key_id=db_api_key.id,
        requested_model=requested_model,
        event_type="stream_new_session_creation_start",
    )
    
    try:
        async with get_db() as db:
            new_session = await session_service.get_session_for_api_key(
                db=db,
                api_key_id=db_api_key.id,
                user_id=user.id,
                requested_model=requested_model,
            )
            new_session_id = new_session.id if new_session else None
        
        if not new_session_id:
            logger.error(
                "Failed to create new session - automation may be disabled",
                event_type="stream_new_session_creation_failed",
            )
            yield StreamResult(success=False, error="Failed to create new session")
            return
        
        logger.info(
            "Created new session for stream retry",
            new_session_id=new_session_id,
            event_type="stream_new_session_created",
        )
        
        await asyncio.sleep(1.0)
        
        logger.info(
            "Retrying stream request with new session",
            new_session_id=new_session_id,
            original_session_id=original_session_id,
            event_type="stream_retry_start",
        )
        
        async for chunk in _process_stream_request(
            session_id=new_session_id,
            messages=messages,
            chat_params=chat_params,
            logger=logger.bind(retry_session_id=new_session_id),
            accumulator=accumulator,
        ):
            yield chunk
            
    except Exception as e:
        logger.error(
            "Failed to create new session",
            error=str(e),
            event_type="stream_new_session_creation_failed",
        )
        yield StreamResult(success=False, error=str(e))


__all__ = [
    "build_stream_generator",
    "StreamingBillingParams",
    "StreamingUsageAccumulator",
    "parse_sse_usage",
]
