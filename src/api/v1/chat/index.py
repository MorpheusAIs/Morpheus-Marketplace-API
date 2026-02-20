# Chat routes 
"""
This module handles chat completion endpoints for the API gateway.

Key behaviors:
- Respects client's 'stream' parameter in requests (true/false)
- Returns streaming responses only when requested (stream=true)
- Returns regular JSON responses when streaming is not requested (stream=false)
- Warning: Tool calling may require streaming with some models
- Billing: Both streaming and non-streaming requests check balance and create usage holds
- Rate limiting: RPM and TPM limits are applied before processing
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse, JSONResponse

from typing import Optional
import json
import uuid

from ....dependencies import get_api_key_user, get_current_api_key
from ....db.database import get_db
from ....db.models import User, APIKey
from ....core.model_routing import model_router
from ....services import session_routing_service, NoSessionAvailableError, SessionOpenError
from ....services.billing_service import billing_service
from ....services.token_estimation_service import token_estimation_service
from ....services.rate_limiting import (
    rate_limit_service,
    RateLimitResult,
    RateLimitStatus,
)
from ....schemas.billing import UsageHoldRequest, UsageFinalizeRequest, UsageVoidRequest
from ....core.logging_config import get_api_logger
from .chat_models import ChatCompletionRequest
from .chat_utils import (
    fix_tool_choice_structure,
    remove_tool_choice_from_tools,
    normalize_assistant_tool_call_messages,
    log_tool_request_details,
)
from .chat_streaming import build_stream_generator, StreamingBillingParams
from .chat_non_streaming import handle_non_streaming_request
from .chat_exceptions import (
    ChatError,
    InsufficientBalanceError,
    BillingError,
    SessionNotFoundError,
    SessionCreationError,
    GatewayError,
    RateLimitError,
    handle_chat_error,
)

router = APIRouter(tags=["Chat"])


@router.post("/completions")
async def create_chat_completion(
    request_data: ChatCompletionRequest,
    request: Request,
    user: User = Depends(get_api_key_user),
    db_api_key: APIKey = Depends(get_current_api_key)
):
    """
    Create a chat completion with automatic session creation if enabled.
    
    Supports both streaming and non-streaming responses based on the 'stream' parameter.
    Tool calling is supported but may work better with streaming enabled.
    
    Billing is applied to both streaming and non-streaming requests:
    - Balance is checked before the request starts
    - 402 Payment Required is returned if insufficient credits
    - Usage is finalized after completion with actual token counts
    - Holds are voided on failures or disconnects
    """
    logger = get_api_logger()
    request_id = str(uuid.uuid4())[:8]
    
    chat_logger = logger.bind(
        endpoint="create_chat_completion",
        user_id=user.id,
        request_id=request_id,
    )
    
    chat_logger.info(
        "New chat completion request received",
        model=request_data.model,
        stream_requested=request_data.stream,
        event_type="chat_completion_request_start",
    )
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    chat_logger.info(
        "Processing chat completion request for user",
        api_key_id=db_api_key.id,
        event_type="chat_request_processing",
    )
    
    json_body = request_data.model_dump(exclude_none=True)
    
    # Check rate limits before processing
    rate_limit_result = await _check_rate_limits(
        user=user,
        db_api_key=db_api_key,
        model=request_data.model,
        messages=json_body.get("messages", []),
        chat_logger=chat_logger,
        request_id=request_id,
    )
    has_tools = bool(json_body.get("tools"))
    should_stream = request_data.stream or False
    
    if has_tools and not should_stream:
        chat_logger.warning(
            "Tool calling requested with stream=false - this may cause issues with some models",
            has_tools=has_tools,
            stream_enabled=should_stream,
            event_type="tool_calling_stream_warning",
        )
    
    json_body["stream"] = should_stream

    requested_model = json_body.pop("model", None)
    
    if json_body.get("tools"):
        chat_logger.info(
            "Request includes tools",
            tool_count=len(json_body["tools"]),
            event_type="tools_detected",
        )
    if json_body.get("tool_choice"):
        chat_logger.info(
            "Request includes tool_choice",
            tool_choice=json_body["tool_choice"],
            event_type="tool_choice_detected",
        )
    
    body = json.dumps(json_body).encode("utf-8")
        
    # Create billing hold
    ledger_entry_id, model_id, token_estimate = await _create_billing_hold(
        request_id=request_id,
        requested_model=requested_model,
        json_body=json_body,
        db_api_key=db_api_key,
        user=user,
        chat_logger=chat_logger,
    )
    
    # Get or create session (void billing hold on failure)
    try:
        session_id = await _resolve_session(
            db_api_key=db_api_key,
            user=user,
            requested_model=requested_model,
            chat_logger=chat_logger,
            request_id=request_id,
        )
    except Exception as e:
        await _void_billing_hold(
            user_id=user.id,
            ledger_entry_id=ledger_entry_id,
            failure_code="session_error",
            failure_reason=str(e),
            chat_logger=chat_logger,
        )
        raise
    
    chat_logger.info(
        "Original request details",
        session_id=session_id,
        requested_model=requested_model,
        event_type="request_details",
    )
    
    # Apply request fixes for tool calling compatibility
    fix_tool_choice_structure(json_body, chat_logger)
    remove_tool_choice_from_tools(json_body, chat_logger)
    normalize_assistant_tool_call_messages(json_body, chat_logger)
    log_tool_request_details(json_body, session_id, chat_logger)
    
    # Handle request based on streaming preference
    if should_stream:
        return _handle_streaming_request(
            chat_logger=chat_logger,
            request_id=request_id,
            session_id=session_id,
            body=body,
            requested_model=requested_model,
            model_id=model_id,
            db_api_key=db_api_key,
            user=user,
            ledger_entry_id=ledger_entry_id,
            token_estimate=token_estimate,
            rate_limit_result=rate_limit_result,
        )
    else:
        return await _handle_non_streaming_request(
            chat_logger=chat_logger,
            request_id=request_id,
            session_id=session_id,
            body=body,
            requested_model=requested_model,
            model_id=model_id,
            db_api_key=db_api_key,
            user=user,
            ledger_entry_id=ledger_entry_id,
            rate_limit_result=rate_limit_result,
        )


async def _check_rate_limits(
    user: User,
    db_api_key: APIKey,
    model: Optional[str],
    messages: list,
    chat_logger,
    request_id: str,
) -> RateLimitResult:
    """
    Check rate limits (RPM and TPM) before processing the request.
    
    Args:
        user: The authenticated user
        db_api_key: The API key used for the request
        model: The requested model
        messages: The chat messages
        chat_logger: Logger instance
        request_id: Unique request ID
        
    Returns:
        RateLimitResult with current usage information
        
    Raises:
        RateLimitError: If rate limit is exceeded
    """
    # Estimate input tokens for TPM check (~4 chars per token)
    total_chars = sum(
        len(str(m.get("content", ""))) 
        for m in messages if isinstance(m, dict)
    )
    estimated_tokens = max(total_chars // 4, 1)
    
    # Use API key prefix as user identifier for rate limiting
    user_identifier = f"key:{db_api_key.key_prefix}"
    
    result = await rate_limit_service.check_rate_limit(
        user_id=user_identifier,
        model=model,
        estimated_tokens=estimated_tokens,
        request_id=request_id,
    )
    
    if not result.allowed and result.status != RateLimitStatus.ERROR:
        limit_type = "rpm" if result.status == RateLimitStatus.EXCEEDED_RPM else "tpm"
        
        chat_logger.warning(
            "Rate limit exceeded",
            user_id=user.id,
            api_key_prefix=db_api_key.key_prefix,
            model=model,
            model_group=result.model_group,
            limit_type=limit_type,
            rpm_current=result.rpm_current,
            rpm_limit=result.rpm_limit,
            tpm_current=result.tpm_current,
            tpm_limit=result.tpm_limit,
            retry_after=result.retry_after,
            event_type="rate_limit_exceeded",
        )
        
        raise RateLimitError(
            rpm_current=result.rpm_current,
            rpm_limit=result.rpm_limit,
            tpm_current=result.tpm_current,
            tpm_limit=result.tpm_limit,
            retry_after=result.retry_after,
            reset_at=result.reset_at,
            limit_type=limit_type,
        )
    
    chat_logger.debug(
        "Rate limit check passed",
        rpm_remaining=result.rpm_remaining,
        tpm_remaining=result.tpm_remaining,
        model_group=result.model_group,
        event_type="rate_limit_check_passed",
    )
    
    return result


async def _resolve_session(
    db_api_key: APIKey,
    user: User,
    requested_model: Optional[str],
    chat_logger,
    request_id: str,
) -> str:
    """Resolve or create a session for the request using the Session Routing Service."""

    chat_logger.info(
        "No session_id in request, routing to session via SessionRoutingService",
        api_key_id=db_api_key.id,
        requested_model=requested_model,
        event_type="session_routing_start",
    )
    
    try:
        async with get_db() as db:
            routed_session_id = await session_routing_service.route_request(
                db=db,
                user_id=user.id,
                requested_model=requested_model,
                model_type="LLM"
            )
            chat_logger.info(
                "Session routed successfully",
                session_id=routed_session_id,
                event_type="session_routing_success",
            )
            return routed_session_id
    except NoSessionAvailableError as e:
        raise SessionNotFoundError() from e
    except SessionOpenError as e:
        raise SessionCreationError(
            message=f"Error opening session: {e.message}",
        ) from e
    except Exception as e:
        raise SessionCreationError(
            message=f"Error handling session: {e}",
        ) from e


async def _create_billing_hold(
    request_id: str,
    requested_model: Optional[str],
    json_body: dict,
    db_api_key: APIKey,
    user: User,
    chat_logger,
) -> tuple[uuid.UUID, Optional[str], any]:
    """Create a billing hold for the request. Raises on failure."""
    ledger_entry_id = uuid.uuid4()
    chat_logger = chat_logger.bind(ledger_entry_id=str(ledger_entry_id))
    
    try:
        model_id = await model_router.get_target_model(requested_model, type="LLM")
        token_estimate = token_estimation_service.estimate(json_body, model_type="LLM")
        
        async with get_db() as db:
            hold_request = UsageHoldRequest(
                ledger_entry_id=ledger_entry_id,
                request_id=request_id,
                estimated_input_tokens=token_estimate.input_tokens,
                estimated_output_tokens=token_estimate.output_tokens,
                api_key_id=db_api_key.id,
                model_name=requested_model,
                model_id=model_id,
                endpoint="/v1/chat/completions",
            )
            hold_response = await billing_service.create_usage_hold(db, user.id, hold_request)
        
        if not hold_response.success:
            if hold_response.error == "insufficient_balance":
                raise InsufficientBalanceError(
                    available_balance=str(hold_response.available_balance),
                    estimated_cost=str(hold_response.estimated_cost),
                )
            raise BillingError(message=f"Billing error: {hold_response.error}")
        
        chat_logger.info(
            "Usage hold created",
            hold_amount=str(hold_response.hold_amount),
            estimated_cost=str(hold_response.estimated_cost),
            event_type="usage_hold_created",
        )
        
        return ledger_entry_id, model_id, token_estimate
        
    except ChatError:
        raise
    except Exception as e:
        raise GatewayError(message=f"Error in API gateway: {e}") from e


def _handle_streaming_request(
    chat_logger,
    request_id: str,
    session_id: str,
    body: bytes,
    requested_model: Optional[str],
    model_id: Optional[str],
    db_api_key: APIKey,
    user: User,
    ledger_entry_id: uuid.UUID,
    token_estimate,
    rate_limit_result: Optional[RateLimitResult] = None,
) -> StreamingResponse:
    """Handle streaming chat completion request (hold already created)."""
    chat_logger.info(
        "Processing streaming request",
        session_id=session_id,
        event_type="streaming_request_start",
    )
    
    # Build rate limit user identifier for token tracking
    rate_limit_user_id = f"key:{db_api_key.key_prefix}" if db_api_key else None
    
    billing_params = StreamingBillingParams(
        user_id=user.id,
        api_key_id=db_api_key.id,
        model_name=requested_model,
        model_id=model_id,
        estimated_input_tokens=token_estimate.input_tokens,
        estimated_output_tokens=token_estimate.output_tokens,
        rate_limit_user_id=rate_limit_user_id,
        request_id=request_id,
    )
    
    stream_generator = build_stream_generator(
        logger=chat_logger,
        session_id=session_id,
        body=body,
        requested_model=requested_model,
        db_api_key=db_api_key,
        user=user,
        ledger_entry_id=ledger_entry_id,
        billing_params=billing_params,
    )
    
    chat_logger.info(
        "Returning streaming response",
        session_id=session_id,
        event_type="streaming_response_start",
    )
    
    # Build headers including rate limit info
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Request-Id": request_id,
    }
    
    # Add rate limit headers if available
    if rate_limit_result and rate_limit_result.rpm_limit > 0:
        rate_headers = rate_limit_service.create_rate_limit_headers(rate_limit_result)
        headers.update(rate_headers.to_dict())
    
    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers=headers,
    )


async def _handle_non_streaming_request(
    chat_logger,
    request_id: str,
    session_id: str,
    body: bytes,
    requested_model: Optional[str],
    model_id: Optional[str],
    db_api_key: APIKey,
    user: User,
    ledger_entry_id: uuid.UUID,
    rate_limit_result: Optional[RateLimitResult] = None,
) -> JSONResponse:
    """Handle non-streaming chat completion request (hold already created)."""
    chat_logger.info(
        "Processing non-streaming request",
        session_id=session_id,
        event_type="non_streaming_request_start",
    )
    
    try:
        response = await handle_non_streaming_request(
            logger=chat_logger,
            request_id=request_id,
            body=body,
            db_api_key=db_api_key,
            user=user,
            requested_model=requested_model,
            session_id=session_id,
        )
        
        if response.status_code == 200:
            # Finalize billing and record actual token usage
            updated_rate_limit_result = await _finalize_billing(
                response=response,
                ledger_entry_id=ledger_entry_id,
                requested_model=requested_model,
                model_id=model_id,
                user=user,
                chat_logger=chat_logger,
                db_api_key=db_api_key,
                request_id=request_id,
            )
            
            # Add rate limit headers AFTER recording actual tokens
            # Use updated result if available, otherwise fall back to initial result
            headers_result = updated_rate_limit_result or rate_limit_result
            if headers_result and headers_result.rpm_limit > 0:
                rate_headers = rate_limit_service.create_rate_limit_headers(headers_result)
                for key, value in rate_headers.to_dict().items():
                    response.headers[key] = value
        else:
            await _void_billing_hold(
                user_id=user.id,
                ledger_entry_id=ledger_entry_id,
                failure_code=str(response.status_code),
                failure_reason="Request failed",
                chat_logger=chat_logger,
            )
            # For failed requests, still add rate limit headers from initial check
            if rate_limit_result and rate_limit_result.rpm_limit > 0:
                rate_headers = rate_limit_service.create_rate_limit_headers(rate_limit_result)
                for key, value in rate_headers.to_dict().items():
                    response.headers[key] = value
        
        response.headers["X-Request-Id"] = request_id
        return response
        
    except Exception as e:
        await _void_billing_hold(
            user_id=user.id,
            ledger_entry_id=ledger_entry_id,
            failure_code="exception",
            failure_reason=str(e),
            chat_logger=chat_logger,
        )
        raise GatewayError(message=f"Error in API gateway: {e}", session_id=session_id) from e
    
    finally:
        # Release the session after non-streaming request completes
        try:
            async with get_db() as db:
                await session_routing_service.release_session(db, session_id)
                chat_logger.debug(
                    "Session released after non-streaming request",
                    session_id=session_id,
                    event_type="session_released",
                )
        except Exception as release_err:
            chat_logger.warning(
                "Failed to release session",
                session_id=session_id,
                error=str(release_err),
                event_type="session_release_error",
            )


async def _finalize_billing(
    response: JSONResponse,
    ledger_entry_id: uuid.UUID,
    requested_model: Optional[str],
    model_id: Optional[str],
    user: User,
    chat_logger,
    db_api_key: Optional[APIKey] = None,
    request_id: Optional[str] = None,
) -> Optional[RateLimitResult]:
    """
    Finalize billing after successful response and record actual token usage for rate limiting.
    
    Returns:
        Updated RateLimitResult with actual token counts, or None if rate limiting is disabled.
    """
    updated_rate_limit_result = None
    
    try:
        response_body = json.loads(response.body.decode("utf-8"))
        usage = response_body.get("usage_from_provider", {})
        tokens_input = usage.get("prompt_tokens", 0)
        tokens_output = usage.get("completion_tokens", 0)
        tokens_total = usage.get("total_tokens", tokens_input + tokens_output)
        
        # Record actual token usage for rate limiting and get updated result
        if db_api_key and tokens_total > 0:
            user_identifier = f"key:{db_api_key.key_prefix}"
            updated_rate_limit_result = await rate_limit_service.record_token_usage(
                user_id=user_identifier,
                input_tokens=tokens_input,
                output_tokens=tokens_output,
                model=requested_model,
                request_id=request_id,
            )
            chat_logger.debug(
                "Recorded actual token usage for rate limiting",
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                tokens_total=tokens_total,
                tpm_current=updated_rate_limit_result.tpm_current if updated_rate_limit_result else None,
                event_type="rate_limit_tokens_recorded",
            )
        
        async with get_db() as db:
            finalize_request = UsageFinalizeRequest(
                ledger_entry_id=ledger_entry_id,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                tokens_total=tokens_total,
                model_name=requested_model,
                model_id=model_id,
                endpoint="/v1/chat/completions",
            )
            finalize_response = await billing_service.finalize_usage(
                db, user.id, finalize_request
            )
            
            chat_logger.info(
                "Usage finalized",
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                amount_total=str(finalize_response.amount_total),
                event_type="usage_finalized",
            )
        
        return updated_rate_limit_result
        
    except Exception as e:
        chat_logger.error(
            "Error finalizing usage",
            error=str(e),
            event_type="usage_finalize_error",
            exc_info=True,
        )
        return None


async def _void_billing_hold(
    user_id: int,
    ledger_entry_id: uuid.UUID,
    failure_code: str,
    failure_reason: str,
    chat_logger,
) -> None:
    """Void a billing hold on failure."""
    try:
        async with get_db() as db:
            void_request = UsageVoidRequest(
                ledger_entry_id=ledger_entry_id,
                failure_code=failure_code,
                failure_reason=failure_reason,
            )
            await billing_service.void_usage(db, user_id, void_request)
            chat_logger.info(
                "Usage hold voided",
                failure_code=failure_code,
                event_type="usage_voided",
            )
    except Exception as e:
        chat_logger.error(
            "Error voiding usage hold",
            error=str(e),
            event_type="usage_void_error",
            exc_info=True,
        )
