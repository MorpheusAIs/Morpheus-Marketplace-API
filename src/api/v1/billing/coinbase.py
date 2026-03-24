"""
Coinbase Business Payment Link billing endpoints.
Authenticated user endpoints for creating payment links and checking status.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from ....db.models import User
from ....dependencies import get_current_user
from ....schemas.payment_link import (
    CreatePaymentLinkRequest,
    PaymentLinkResponse,
)
from ....core.logging_config import get_api_logger
from ....services.coinbase_payment_link_service import coinbase_payment_link_service

logger = get_api_logger()

router = APIRouter(prefix="/coinbase", tags=["Coinbase Billing"])


@router.post("/payment-links", response_model=PaymentLinkResponse)
async def create_payment_link(
    request: CreatePaymentLinkRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Create a Coinbase Business Payment Link for the authenticated user.

    Creates a USDC payment link via the Coinbase Business API.
    The user_id is automatically added to metadata so the webhook
    can credit the correct account.
    """
    try:
        metadata = request.metadata or {}
        metadata["user_id"] = current_user.cognito_user_id

        result = await coinbase_payment_link_service.create_payment_link(
            amount=request.amount,
            currency=request.currency,
            metadata=metadata,
            description=request.description,
            success_redirect_url=request.success_redirect_url,
            failure_redirect_url=request.failure_redirect_url,
            expires_at=request.expires_at,
        )

        logger.info(
            "Payment link created",
            user_id=current_user.id,
            payment_link_id=result.get("id"),
            amount=request.amount,
            currency=request.currency,
            event_type="coinbase_payment_link_created",
        )

        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except Exception as e:
        logger.error(
            "Error creating payment link",
            error=str(e),
            error_type=type(e).__name__,
            event_type="coinbase_payment_link_error",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating payment link: {str(e)}",
        )


@router.get("/payment-links/{payment_link_id}", response_model=PaymentLinkResponse)
async def get_payment_link(
    payment_link_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get a specific Coinbase Business Payment Link by ID.
    """
    try:
        result = await coinbase_payment_link_service.get_payment_link(payment_link_id)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except Exception as e:
        logger.error(
            "Error getting payment link",
            payment_link_id=payment_link_id,
            error=str(e),
            error_type=type(e).__name__,
            event_type="coinbase_payment_link_get_error",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting payment link: {str(e)}",
        )


coinbase_billing_router = router
