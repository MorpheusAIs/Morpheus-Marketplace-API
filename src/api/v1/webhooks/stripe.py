"""
Stripe webhook endpoint for receiving payment events.

Implements Stripe's best practices:
- Signature verification to ensure events are from Stripe
- Idempotent event handling to prevent duplicate processing
- Fast response with DB commit before returning 200
- Proper error handling and logging

Events handled:
- checkout.session.completed: One-time payments via Checkout or Payment Links
- invoice.paid: Subscription/invoice payments
- invoice.payment_failed: Failed payment attempts

User identification:
- Checkout Sessions (API): Uses metadata.user_id
- Payment Links: Uses client_reference_id
"""
from fastapi import APIRouter, Request, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession
import stripe

from ....db.database import get_db_session
from ....services.stripe_webhook_service import stripe_webhook_service
from ....core.config import settings
from ....core.logging_config import get_core_logger

logger = get_core_logger()

router = APIRouter(tags=["Webhooks"])


async def verify_stripe_signature(request: Request) -> stripe.Event:
    """
    Verify the Stripe webhook signature and parse the event.
    
    This is a critical security measure that ensures events are
    genuinely from Stripe and haven't been tampered with.
    
    Raises:
        HTTPException: If signature verification fails or webhook secret is not configured
    """
    # Check that Stripe webhook secret is configured
    if not settings.STRIPE_WEBHOOK_SECRET:
        logger.error(
            "Stripe webhook received but STRIPE_WEBHOOK_SECRET not configured",
            event_type="stripe_webhook_not_configured",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe webhook not configured"
        )
    
    # Get the raw request body - required for signature verification
    try:
        payload = await request.body()
    except Exception as e:
        logger.error(
            "Failed to read webhook request body",
            error=str(e),
            event_type="stripe_webhook_body_error",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to read request body"
        )
    
    # Get the Stripe signature header
    sig_header = request.headers.get("stripe-signature")
    if not sig_header:
        logger.warning(
            "Stripe webhook missing signature header",
            event_type="stripe_webhook_missing_signature",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header"
        )
    
    # Verify signature and construct event
    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        # Invalid payload
        logger.warning(
            "Stripe webhook invalid payload",
            error=str(e),
            event_type="stripe_webhook_invalid_payload",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload"
        )
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        logger.warning(
            "Stripe webhook signature verification failed",
            error=str(e),
            event_type="stripe_webhook_invalid_signature",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature"
        )
    
    return event


@router.post("/stripe")
async def handle_stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Handle incoming Stripe webhook events.
    
    This endpoint receives webhook events from Stripe for:
    - checkout.session.completed: Successful one-time payments
    - invoice.paid: Successful subscription/invoice payments
    - invoice.payment_failed: Failed payment attempts
    
    The endpoint:
    1. Verifies the webhook signature to ensure authenticity
    2. Processes the event based on its type
    3. Stores changes in the database
    4. Returns 200 only AFTER database commit (per user requirement)
    
    Note: This endpoint is public but protected by Stripe signature verification.
    Only requests signed with the correct webhook secret will be processed.
    """
    # Verify signature and parse event
    event = await verify_stripe_signature(request)
    
    event_id = event.id
    event_type = event.type
    
    logger.info(
        "Received Stripe webhook event",
        stripe_event_id=event_id,
        stripe_event_type=event_type,
    )
    
    # Handle the event based on type
    try:
        if event_type == stripe_webhook_service.EVENT_TYPE_CHECKOUT_SESSION_COMPLETED:
            # One-time payment completed
            session = event.data.object
            success, message = await stripe_webhook_service.handle_checkout_session_completed(
                db=db,
                session=session,
                event_id=event_id,
                event_type=event_type,
            )
            
            if not success:
                logger.error(
                    "Failed to process checkout.session.completed",
                    stripe_event_id=event_id,
                    error=message,
                )
                # Return 200 anyway to acknowledge receipt
                # Stripe will not retry if we return 200
                # We log the error for manual investigation
            
        elif event_type == stripe_webhook_service.EVENT_TYPE_INVOICE_PAID:
            # Invoice payment succeeded (subscription or one-time invoice)
            invoice = event.data.object
            success, message = await stripe_webhook_service.handle_invoice_paid(
                db=db,
                invoice=invoice,
                event_id=event_id,
                event_type=event_type,
            )
            
            if not success:
                logger.error(
                    "Failed to process invoice.paid",
                    stripe_event_id=event_id,
                    error=message,
                )
            
        elif event_type == stripe_webhook_service.EVENT_TYPE_INVOICE_PAYMENT_FAILED:
            # Invoice payment failed
            invoice = event.data.object
            success, message = await stripe_webhook_service.handle_invoice_payment_failed(
                db=db,
                invoice=invoice,
            )
            
            if not success:
                logger.error(
                    "Failed to process invoice.payment_failed",
                    stripe_event_id=event_id,
                    error=message,
                )
            
        else:
            # Log unhandled event types for visibility
            logger.info(
                "Received unhandled Stripe event type",
                stripe_event_id=event_id,
                stripe_event_type=event_type,
            )
        
        # Return success response
        # Note: DB changes are already committed by the service layer
        return {"received": True}
        
    except Exception as e:
        # Log the error with full context
        logger.exception(
            "Error processing Stripe webhook event",
            stripe_event_id=event_id,
            stripe_event_type=event_type,
            error=str(e),
        )
        
        # Re-raise as HTTP exception
        # Return 500 so Stripe will retry the webhook
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error processing webhook"
        )


# Export router
stripe_webhook_router = router

