"""
Coinbase Commerce webhook endpoint for receiving payment events.

Implements Coinbase's best practices:
- Signature verification to ensure events are from Coinbase
- Idempotent event handling
- Fast response
"""
import hmac
import hashlib
import json
from fastapi import APIRouter, Request, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.database import get_db_session
from ....services.coinbase_webhook_service import coinbase_webhook_service
from ....core.config import settings
from ....core.logging_config import get_core_logger

logger = get_core_logger()

router = APIRouter(tags=["Webhooks"])


async def verify_coinbase_signature(request: Request, body_bytes: bytes) -> None:
    """
    Verify the Coinbase webhook signature.
    
    Raises:
        HTTPException: If verification fails
    """
    if not settings.COINBASE_COMMERCE_WEBHOOK_SECRET:
        logger.error(
            "Coinbase webhook received but COINBASE_COMMERCE_WEBHOOK_SECRET not configured",
            event_type="coinbase_webhook_not_configured",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Coinbase webhook not configured"
        )
        
    sig_header = request.headers.get("x-cc-webhook-signature")
    if not sig_header:
        logger.warning(
            "Coinbase webhook missing signature header",
            event_type="coinbase_webhook_missing_signature",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-CC-Webhook-Signature header"
        )
        
    try:
        # Create the HMAC signature
        signature = hmac.new(
            settings.COINBASE_COMMERCE_WEBHOOK_SECRET.encode('utf-8'),
            body_bytes,
            digestmod=hashlib.sha256
        ).hexdigest()
        
        # Compare signatures securely
        if not hmac.compare_digest(signature, sig_header):
            logger.warning(
                "Coinbase webhook signature verification failed",
                event_type="coinbase_webhook_invalid_signature",
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid signature"
            )
            
    except Exception as e:
        logger.error(
            "Error verifying Coinbase signature",
            error=str(e),
            event_type="coinbase_webhook_verification_error",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signature verification error"
        )


@router.post("/coinbase")
async def handle_coinbase_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Handle incoming Coinbase Commerce webhook events.
    
    Handles:
    - charge:confirmed: Successful payments
    
    The endpoint:
    1. Verifies the webhook signature (X-CC-Webhook-Signature)
    2. Processes the event
    3. Stores changes in database
    """
    # Read body once for verification and parsing
    try:
        body_bytes = await request.body()
        await verify_coinbase_signature(request, body_bytes)
        
        # Parse JSON body
        event = json.loads(body_bytes)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to read/parse webhook body",
            error=str(e),
            event_type="coinbase_webhook_body_error",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request body"
        )
        
    event_id = event.get("id")
    event_type = event.get("type")
    
    if not event_id or not event_type:
        logger.warning(
            "Invalid Coinbase event format (missing id or type)",
            event_type="coinbase_webhook_invalid_format"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid event format"
        )
        
    logger.info(
        "Received Coinbase webhook event",
        coinbase_event_id=event_id,
        coinbase_event_type=event_type,
    )
    
    try:
        event_data = event.get("data", {})
        
        if event_type == coinbase_webhook_service.EVENT_TYPE_CHARGE_CONFIRMED:
            # Payment confirmed
            success, message = await coinbase_webhook_service.handle_charge_confirmed(
                db=db,
                event_data=event_data,
                event_id=event_id,
                event_type=event_type,
            )
            
            if not success:
                logger.error(
                    "Failed to process charge:confirmed",
                    coinbase_event_id=event_id,
                    error=message,
                )
                
        else:
            # Log unhandled event types
            logger.info(
                "Received unhandled Coinbase event type",
                coinbase_event_id=event_id,
                coinbase_event_type=event_type,
            )
            
        return {"received": True}
        
    except Exception as e:
        logger.exception(
            "Error processing Coinbase webhook event",
            coinbase_event_id=event_id,
            coinbase_event_type=event_type,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error processing webhook"
        )


# Export router
coinbase_webhook_router = router
