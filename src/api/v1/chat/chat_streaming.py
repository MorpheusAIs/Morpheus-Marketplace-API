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
from ....services import session_routing_service
from ....services.billing_service import billing_service
from ....services.rate_limiting import rate_limit_service
from ....schemas.billing import UsageFinalizeRequest, UsageVoidRequest
from ....db.database import get_db
from ....utils.error_sanitizer import sanitize_error_message
from ....db.models import SessionState
from . import chat_failover

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
    rate_limit_user_id: Optional[str] = None
    request_id: Optional[str] = None


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
    needs_retry: bool = False      # expired-session renewal mechanism
    needs_failover: bool = False   # provider became unavailable pre-first-token


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


async def _release_session_quiet(session_id: str, logger) -> None:
    """Release a session's request slot, logging (not raising) failures."""
    try:
        async with get_db() as db:
            await session_routing_service.release_session(db, session_id)
    except Exception as release_err:
        logger.warning(
            "Failed to release retry session",
            session_id=session_id,
            error=str(release_err),
            event_type="session_release_error",
        )


async def _finalize_streaming_billing(
    user_id: int,
    ledger_entry_id: uuid.UUID,
    accumulator: StreamingUsageAccumulator,
    logger: "BoundLogger",
    rate_limit_user_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """Finalize billing after successful stream completion and record actual token usage for rate limiting."""
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

        # Record actual token usage for rate limiting
        if rate_limit_user_id and accumulator.tokens_total > 0:
            await rate_limit_service.record_token_usage(
                user_id=rate_limit_user_id,
                input_tokens=accumulator.tokens_input,
                output_tokens=accumulator.tokens_output,
                model=accumulator.model_name,
                request_id=request_id,
            )
            logger.debug(
                "Recorded actual streaming token usage for rate limiting",
                tokens_input=accumulator.tokens_input,
                tokens_output=accumulator.tokens_output,
                tokens_total=accumulator.tokens_total,
                event_type="rate_limit_streaming_tokens_recorded",
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


async def _stream_cleanup(
    *,
    session_id: str,
    stream_completed_successfully: bool,
    stream_error: Optional[str],
    billing_enabled: bool,
    ledger_entry_id: Optional[uuid.UUID],
    billing_params: Optional[StreamingBillingParams],
    accumulator: Optional[StreamingUsageAccumulator],
    logger: "BoundLogger",
) -> None:
    """
    Post-stream cleanup: release session and finalize/void billing.

    This function is designed to be called via ``asyncio.shield()`` so that
    it runs to completion even when the parent streaming task has been
    cancelled (e.g. client disconnect).
    """
    try:
        async with get_db() as db:
            await session_routing_service.release_session(db, session_id)
            logger.debug(
                "Session released after streaming",
                session_id=session_id,
                event_type="session_released_after_stream",
            )
    except Exception as release_err:
        logger.warning(
            "Failed to release session after streaming",
            session_id=session_id,
            error=str(release_err),
            event_type="session_release_error",
        )

    if billing_enabled and ledger_entry_id and billing_params:
        if stream_completed_successfully and accumulator:
            await _finalize_streaming_billing(
                user_id=billing_params.user_id,
                ledger_entry_id=ledger_entry_id,
                accumulator=accumulator,
                logger=logger,
                rate_limit_user_id=billing_params.rate_limit_user_id,
                request_id=billing_params.request_id,
            )
        else:
            await _void_streaming_billing(
                user_id=billing_params.user_id,
                ledger_entry_id=ledger_entry_id,
                failure_code="stream_error" if stream_error else "stream_incomplete",
                failure_reason=stream_error or "Stream did not complete successfully",
                logger=logger,
            )


def build_stream_generator(
    *,
    logger: "BoundLogger",
    session_id: str,
    body: bytes,
    requested_model: Optional[str],
    model_id: Optional[str] = None,
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
        model_id: Model ID for failover routing
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
                request_id=billing_params.request_id if billing_params else None,
            ):
                if isinstance(chunk_data, StreamResult):
                    chunk_count = chunk_data.chunk_count
                    stream_completed_successfully = chunk_data.success
                    stream_error = chunk_data.error

                    if (chunk_data.needs_retry or chunk_data.needs_failover) and db_api_key and user:
                        if chunk_data.needs_retry:
                            # Expired-session renewal (existing mechanism).
                            retry_gen = _handle_session_retry(
                                original_session_id=session_id,
                                messages=messages,
                                chat_params=chat_params,
                                db_api_key=db_api_key,
                                user=user,
                                requested_model=requested_model,
                                logger=stream_logger,
                                accumulator=accumulator,
                                request_id=billing_params.request_id if billing_params else None,
                            )
                        else:
                            # Provider failover (different provider).
                            retry_gen = _handle_failover_retry(
                                original_session_id=session_id,
                                messages=messages,
                                chat_params=chat_params,
                                user=user,
                                requested_model=requested_model,
                                model_id=model_id,
                                failure_reason=chunk_data.error or "",
                                logger=stream_logger,
                                accumulator=accumulator,
                                request_id=billing_params.request_id if billing_params else None,
                            )
                        async for retry_chunk in retry_gen:
                            if isinstance(retry_chunk, StreamResult):
                                chunk_count = retry_chunk.chunk_count
                                stream_completed_successfully = retry_chunk.success
                                stream_error = retry_chunk.error
                            else:
                                yield retry_chunk
                    elif chunk_data.needs_retry or chunk_data.needs_failover:
                        # No auth context to reroute with — surface the error.
                        yield _format_sse_error(
                            "proxy_error",
                            sanitize_error_message(chunk_data.error or "provider failure"),
                            session_id=session_id,
                        )
                else:
                    yield chunk_data

        except asyncio.CancelledError:
            stream_error = "client_disconnected"
            stream_logger.warning(
                "Stream cancelled due to client disconnect",
                chunk_count=chunk_count,
                event_type="stream_client_disconnected",
            )

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
            # Shield cleanup from task cancellation so that billing
            # void/finalize and session release always run to completion,
            # even after a client disconnect triggers CancelledError.
            try:
                await asyncio.shield(
                    _stream_cleanup(
                        session_id=session_id,
                        stream_completed_successfully=stream_completed_successfully,
                        stream_error=stream_error,
                        billing_enabled=billing_enabled,
                        ledger_entry_id=ledger_entry_id,
                        billing_params=billing_params,
                        accumulator=accumulator,
                        logger=stream_logger,
                    )
                )
            except asyncio.CancelledError:
                stream_logger.info(
                    "Stream cleanup shielded from cancellation, "
                    "billing void/finalize will complete in background",
                    event_type="stream_cleanup_shielded",
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
    request_id: Optional[str] = None,
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
            request_id=request_id,
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
                    yield _format_sse_error("proxy_error", f"Proxy router error: {sanitize_error_message(result.error)}", status=response.status_code)
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
            chunk_count=chunk_count,
            event_type="stream_proxy_router_error",
        )
        if chunk_count == 0 and "session expired" in str(e).lower():
            # Separate mechanism: expired-session renewal (trigger was
            # previously unreachable here — real errors raise, they don't
            # return non-200 responses).
            yield StreamResult(success=False, chunk_count=0, error=str(e), needs_retry=True)
            return
        if chunk_count == 0 and chat_failover.is_failover_eligible(e):
            # Provider unavailable and nothing reached the client yet — a
            # transparent failover retry is possible.
            yield StreamResult(success=False, chunk_count=0, error=str(e), needs_failover=True)
            return
        yield _format_sse_error(e.error_type, sanitize_error_message(str(e)), status=e.status_code)
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
    request_id: Optional[str] = None,
) -> AsyncIterator[bytes | StreamResult]:
    """Handle session expiry by routing to a new session and retrying."""
    logger.info(
        "Routing to new session to replace expired session",
        user_id=user.id,
        api_key_id=db_api_key.id,
        requested_model=requested_model,
        event_type="stream_new_session_creation_start",
    )

    new_session_id = None
    try:
        async with get_db() as db:
            # Mark the expired session EXPIRED (not just released): its DB
            # expires_at may still be in the future, and route_request only
            # skips non-OPEN rows. The original's active_requests release
            # is owned by _stream_cleanup.
            await session_routing_service.invalidate_session(
                db,
                original_session_id,
                "session expired on proxy",
                state=SessionState.EXPIRED,
            )

            # Route to a new session
            new_session_id = await session_routing_service.route_request(
                db=db,
                user_id=user.id,
                requested_model=requested_model,
                model_type="LLM",
            )

        if not new_session_id:
            logger.error(
                "Failed to route to new session",
                event_type="stream_new_session_creation_failed",
            )
            yield _format_sse_error(
                "retry_failed",
                "Failed to create new session after session expiry",
                session_id=original_session_id,
            )
            yield StreamResult(success=False, error="Failed to create new session")
            return

        logger.info(
            "Routed to new session for stream retry",
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
            request_id=request_id,
        ):
            if isinstance(chunk, StreamResult) and (chunk.needs_retry or chunk.needs_failover):
                yield _format_sse_error(
                    "retry_failed",
                    sanitize_error_message(chunk.error or "retry failed"),
                    session_id=new_session_id,
                )
                yield StreamResult(success=False, chunk_count=chunk.chunk_count, error=chunk.error)
            else:
                yield chunk

    except Exception as e:
        logger.error(
            "Failed to route to new session",
            error=str(e),
            event_type="stream_new_session_creation_failed",
        )
        yield _format_sse_error(
            "retry_failed",
            sanitize_error_message(str(e)),
            session_id=original_session_id,
        )
        yield StreamResult(success=False, error=str(e))
    finally:
        # Shielded so a client disconnect can't leak the new session's slot.
        if new_session_id:
            try:
                await asyncio.shield(_release_session_quiet(new_session_id, logger))
            except asyncio.CancelledError:
                pass


async def _handle_failover_retry(
    original_session_id: str,
    messages: list,
    chat_params: dict,
    user,
    requested_model: Optional[str],
    model_id: Optional[str],
    failure_reason: str,
    logger: "BoundLogger",
    accumulator: Optional[StreamingUsageAccumulator] = None,
    request_id: Optional[str] = None,
) -> AsyncIterator[bytes | StreamResult]:
    """Provider failover for streams: reroute to a different provider and
    retry the stream once (pre-first-token only)."""
    new_session_id = None
    try:
        new_session_id = await chat_failover.attempt_failover(
            original_session_id=original_session_id,
            model_id=model_id,
            requested_model=requested_model,
            user=user,
            logger=logger,
            request_id=request_id,
            failure_reason=failure_reason,
        )

        if not new_session_id:
            logger.error(
                "Failover not possible for stream",
                event_type="stream_failover_unavailable",
            )
            yield _format_sse_error(
                "proxy_error",
                sanitize_error_message(failure_reason or "provider failure, no alternate session"),
                session_id=original_session_id,
            )
            yield StreamResult(success=False, error=failure_reason or "failover unavailable")
            return

        logger.info(
            "Retrying stream request with new session after failover",
            new_session_id=new_session_id,
            original_session_id=original_session_id,
            event_type="stream_failover_retry_start",
        )

        async for chunk in _process_stream_request(
            session_id=new_session_id,
            messages=messages,
            chat_params=chat_params,
            logger=logger.bind(retry_session_id=new_session_id),
            accumulator=accumulator,
            request_id=request_id,
        ):
            if isinstance(chunk, StreamResult) and (chunk.needs_retry or chunk.needs_failover):
                # Single retry only: a second recoverable failure becomes a
                # terminal client-visible error.
                yield _format_sse_error(
                    "retry_failed",
                    sanitize_error_message(chunk.error or "retry failed"),
                    session_id=new_session_id,
                )
                yield StreamResult(success=False, chunk_count=chunk.chunk_count, error=chunk.error)
            else:
                yield chunk

    except Exception as e:
        logger.error(
            "Stream failover retry failed",
            error=str(e),
            event_type="stream_failover_error",
        )
        yield _format_sse_error(
            "retry_failed",
            sanitize_error_message(str(e)),
            session_id=new_session_id or original_session_id,
        )
        yield StreamResult(success=False, error=str(e))
    finally:
        # Shielded so a client disconnect can't leak the new session's slot.
        if new_session_id:
            try:
                await asyncio.shield(_release_session_quiet(new_session_id, logger))
            except asyncio.CancelledError:
                pass


__all__ = [
    "build_stream_generator",
    "StreamingBillingParams",
    "StreamingUsageAccumulator",
    "parse_sse_usage",
]
