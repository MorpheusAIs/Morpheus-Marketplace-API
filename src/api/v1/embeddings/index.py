# Embeddings routes
"""
This module handles embeddings endpoints for the API gateway.

Key behaviors:
- Creates vector embeddings for input text using the Morpheus Network providers
- Automatically manages sessions and routes requests to the appropriate embedding model
- Billing: Checks balance and creates usage holds, finalizes after completion
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import JSONResponse
from typing import Optional
import uuid

from ....schemas.billing import UsageHoldRequest, UsageFinalizeRequest, UsageVoidRequest
from ....core.model_routing import model_router
from ....services import session_routing_service, NoSessionAvailableError, SessionOpenError
from ....services import proxy_router_service
from ....services.billing_service import billing_service
from ....services.token_estimation_service import token_estimation_service
from ....db.database import get_db
from ....dependencies import get_api_key_user, get_current_api_key
from ....db.models import User, APIKey
from ....core.logging_config import get_api_logger

router = APIRouter(tags=["Embeddings"])

logger = get_api_logger()

from .models import EmbeddingRequest, EmbeddingResponse

@router.post("/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(
    request_data: EmbeddingRequest,
    request: Request,
    user: User = Depends(get_api_key_user),
    db_api_key: APIKey = Depends(get_current_api_key),
):
    """
    Create embeddings for the given input text(s).
    
    This endpoint creates embeddings using the Morpheus Network providers.
    It automatically manages sessions and routes requests to the appropriate embedding model.
    
    Billing is applied to all requests:
    - Balance is checked before the request starts
    - 402 Payment Required is returned if insufficient credits
    - Usage is finalized after completion with actual token counts
    - Holds are voided on failures
    """
    request_id = str(uuid.uuid4())[:8]  # Generate short request ID for tracing
    embeddings_logger = logger.bind(endpoint="create_embeddings", 
                                   user_id=user.id, 
                                   model=request_data.model,
                                   request_id=request_id)
    embeddings_logger.info("Embeddings request received",
                          model=request_data.model,
                          input_type=type(request_data.input).__name__,
                          encoding_format=request_data.encoding_format,
                          dimensions=request_data.dimensions,
                          event_type="embeddings_request_start")
    
    requested_model = request_data.model
    ledger_entry_id = None
    model_id = None
    
    try:
        # Create billing hold
        ledger_entry_id, model_id = await _create_billing_hold(
            request_id=request_id,
            requested_model=requested_model,
            request_data=request_data,
            db_api_key=db_api_key,
            user=user,
            embeddings_logger=embeddings_logger,
        )
        
        # Always resolve session through SessionRoutingService
        try:
            embeddings_logger.info("Routing to session via SessionRoutingService",
                       request_id=request_id,
                       api_key_id=db_api_key.id,
                       requested_model=requested_model,
                       event_type="session_routing_start")
            async with get_db() as db:
                session_id = await session_routing_service.route_request(
                    db=db,
                    user_id=user.id,
                    requested_model=requested_model,
                    model_type='EMBEDDINGS'
                )
            
            embeddings_logger.info("Session routed successfully",
                        request_id=request_id,
                        session_id=session_id,
                        event_type="session_routing_success")
        except NoSessionAvailableError as e:
            embeddings_logger.error("No session available",
                            request_id=request_id,
                            error=str(e),
                            event_type="no_session_available")
            await _void_billing_hold(user.id, ledger_entry_id, "no_session", str(e), embeddings_logger)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No session available for embeddings request"
            )
        except SessionOpenError as e:
            embeddings_logger.error("Failed to open session",
                            request_id=request_id,
                            error=str(e),
                            event_type="session_open_error",
                            exc_info=True)
            await _void_billing_hold(user.id, ledger_entry_id, "session_open_error", str(e), embeddings_logger)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error opening session: {e.message}"
            )
        except Exception as e:
            embeddings_logger.error("Error in session handling",
                            request_id=request_id,
                            error=str(e),
                            event_type="session_handling_error",
                            exc_info=True)
            await _void_billing_hold(user.id, ledger_entry_id, "session_error", str(e), embeddings_logger)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error handling session: {str(e)}"
            )
        
        try:
            response = await proxy_router_service.embeddings(
                session_id=session_id,
                input_data=request_data.input,
                encoding_format=request_data.encoding_format,
                dimensions=request_data.dimensions,
                user=request_data.user
            )
            
            embeddings_logger.info("Proxy-router response received",
                                   status_code=response.status_code,
                                   event_type="proxy_response_received")
            
            if response.status_code == 200:
                response_data = response.json()
                
                # Finalize billing with actual token usage
                await _finalize_billing(
                    response_data=response_data,
                    ledger_entry_id=ledger_entry_id,
                    requested_model=requested_model,
                    model_id=model_id,
                    user=user,
                    embeddings_logger=embeddings_logger,
                )
                
                # Count embeddings in response for metrics
                embedding_count = len(response_data.get("data", [])) if "data" in response_data else 0
                embeddings_logger.info("Successfully processed embeddings request",
                                      embedding_count=embedding_count,
                                      session_id=session_id,
                                      event_type="embeddings_success")
                return JSONResponse(content=response_data)
            else:
                embeddings_logger.error("Proxy-router error",
                                       status_code=response.status_code,
                                       error_response=response.text,
                                       session_id=session_id,
                                       event_type="proxy_error")
                
                await _void_billing_hold(
                    user.id, ledger_entry_id, str(response.status_code), "Proxy error", embeddings_logger
                )
                
                # Try to parse error response
                try:
                    error_data = response.json()
                    error_message = error_data.get("detail", error_data.get("error", response.text))
                except:
                    error_message = response.text
                
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Embeddings request failed: {error_message}"
                )
        
        except proxy_router_service.ProxyRouterServiceError as e:
            embeddings_logger.error("Proxy router service error",
                                   error=str(e),
                                   status_code=e.status_code,
                                   error_type=e.error_type,
                                   session_id=session_id,
                                   event_type="proxy_service_error")
            await _void_billing_hold(user.id, ledger_entry_id, e.error_type, e.message, embeddings_logger)
            raise HTTPException(
                status_code=e.get_http_status_code(),
                detail=f"Embeddings request failed: {e.message}"
            )
        
        finally:
            # Release the session after request completes
            if session_id:
                try:
                    async with get_db() as db:
                        await session_routing_service.release_session(db, session_id)
                        embeddings_logger.debug("Session released",
                                               session_id=session_id,
                                               event_type="session_released")
                except Exception as release_err:
                    embeddings_logger.warning("Failed to release session",
                                             session_id=session_id,
                                             error=str(release_err),
                                             event_type="session_release_error")
    
    except HTTPException:
        raise
    except Exception as e:
        embeddings_logger.error("Unexpected error in embeddings endpoint",
                               error=str(e),
                               model=request_data.model,
                               event_type="embeddings_unexpected_error",
                               exc_info=True)
        if ledger_entry_id:
            await _void_billing_hold(user.id, ledger_entry_id, "exception", str(e), embeddings_logger)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


async def _create_billing_hold(
    request_id: str,
    requested_model: Optional[str],
    request_data: EmbeddingRequest,
    db_api_key: APIKey,
    user: User,
    embeddings_logger,
) -> tuple[uuid.UUID, Optional[str]]:
    """Create a billing hold for the embeddings request. Raises on failure."""
    ledger_entry_id = uuid.uuid4()
    embeddings_logger = embeddings_logger.bind(ledger_entry_id=str(ledger_entry_id))
    
    try:
        model_id = await model_router.get_target_model(requested_model, type="EMBEDDINGS")
        
        # Build request body for token estimation
        request_body = {
            "input": request_data.input,
            "model": requested_model,
        }
        token_estimate = token_estimation_service.estimate(request_body, model_type="EMBEDDINGS")
        
        async with get_db() as db:
            hold_request = UsageHoldRequest(
                ledger_entry_id=ledger_entry_id,
                request_id=request_id,
                estimated_input_tokens=token_estimate.input_tokens,
                estimated_output_tokens=token_estimate.output_tokens,
                api_key_id=db_api_key.id,
                model_name=requested_model,
                model_id=model_id,
                endpoint="/v1/embeddings",
            )
            hold_response = await billing_service.create_usage_hold(db, user.id, hold_request)
        
        if not hold_response.success:
            if hold_response.error == "insufficient_balance":
                embeddings_logger.warning(
                    "Insufficient balance for embeddings request",
                    available_balance=str(hold_response.available_balance),
                    estimated_cost=str(hold_response.estimated_cost),
                    event_type="insufficient_balance",
                )
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail={
                        "error": "insufficient_balance",
                        "message": "Insufficient credits for this request",
                        "available_balance": str(hold_response.available_balance),
                        "estimated_cost": str(hold_response.estimated_cost),
                    }
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Billing error: {hold_response.error}"
            )
        
        embeddings_logger.info(
            "Usage hold created",
            hold_amount=str(hold_response.hold_amount),
            estimated_cost=str(hold_response.estimated_cost),
            event_type="usage_hold_created",
        )
        
        return ledger_entry_id, model_id
        
    except HTTPException:
        raise
    except Exception as e:
        embeddings_logger.error(
            "Error creating billing hold",
            error=str(e),
            event_type="billing_hold_error",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error in API gateway: {e}"
        )


async def _finalize_billing(
    response_data: dict,
    ledger_entry_id: uuid.UUID,
    requested_model: Optional[str],
    model_id: Optional[str],
    user: User,
    embeddings_logger,
) -> None:
    """Finalize billing after successful response."""
    try:
        # Get token usage from response
        usage = response_data.get("usage_from_provider", {})
        tokens_input = usage.get("prompt_tokens", 0)
        tokens_output = 0  # Embeddings don't have output tokens
        tokens_total = usage.get("total_tokens", tokens_input)
        
        async with get_db() as db:
            finalize_request = UsageFinalizeRequest(
                ledger_entry_id=ledger_entry_id,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                tokens_total=tokens_total,
                model_name=requested_model,
                model_id=model_id,
                endpoint="/v1/embeddings",
            )
            finalize_response = await billing_service.finalize_usage(
                db, user.id, finalize_request
            )
            
            embeddings_logger.info(
                "Usage finalized",
                tokens_input=tokens_input,
                tokens_total=tokens_total,
                amount_total=str(finalize_response.amount_total),
                event_type="usage_finalized",
            )
    except Exception as e:
        embeddings_logger.error(
            "Error finalizing usage",
            error=str(e),
            event_type="usage_finalize_error",
            exc_info=True,
        )


async def _void_billing_hold(
    user_id: int,
    ledger_entry_id: Optional[uuid.UUID],
    failure_code: str,
    failure_reason: str,
    embeddings_logger,
) -> None:
    """Void a billing hold on failure."""
    if not ledger_entry_id:
        return
    
    try:
        async with get_db() as db:
            void_request = UsageVoidRequest(
                ledger_entry_id=ledger_entry_id,
                failure_code=failure_code,
                failure_reason=failure_reason,
            )
            await billing_service.void_usage(db, user_id, void_request)
            embeddings_logger.info(
                "Usage hold voided",
                failure_code=failure_code,
                event_type="usage_voided",
            )
    except Exception as e:
        embeddings_logger.error(
            "Error voiding usage hold",
            error=str(e),
            event_type="usage_void_error",
            exc_info=True,
        )
