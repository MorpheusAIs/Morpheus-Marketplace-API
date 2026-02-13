"""
Coinbase webhook endpoint for receiving payment events.

Supports both:
- NEW: Payment Link API webhooks (payment_link.payment.success/failed/expired)
       Signature: X-Hook0-Signature header (t=timestamp,h=headers,v1=hmac)
       Docs: https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/webhooks

- LEGACY: Commerce Charge API webhooks (charge:pending, charge:confirmed, etc.)
          Signature: X-CC-Webhook-Signature header (HMAC-SHA256 of body)
          Will be removed after migration is complete.
"""
import hmac
import hashlib
import json
import time
from fastapi import APIRouter, Request, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.database import get_db_session
from ....services.coinbase_webhook_service import coinbase_webhook_service
from ....core.config import settings
from ....core.logging_config import get_core_logger

logger = get_core_logger()

router = APIRouter(tags=["Webhooks"])

# Maximum webhook age in minutes (replay attack protection)
MAX_WEBHOOK_AGE_MINUTES = 5


# === Signature Verification ===


def _detect_webhook_format(request: Request) -> str:
    """
    Detect whether the incoming webhook uses the new Payment Link format
    or legacy Commerce Charge format based on the signature header.

    Returns:
        "payment_link" or "legacy_commerce"
    """
    if request.headers.get("x-hook0-signature"):
        return "payment_link"
    if request.headers.get("x-cc-webhook-signature"):
        return "legacy_commerce"
    return "unknown"


async def verify_payment_link_signature(
    request: Request, body_bytes: bytes
) -> None:
    """
    Verify the Payment Link API webhook signature (X-Hook0-Signature).

    The signature header format: t=<timestamp>,h=<header_names>,v1=<signature>
    Signed payload: "{timestamp}.{header_names}.{header_values}.{body}"

    Also verifies the timestamp to prevent replay attacks.

    See: https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/webhooks

    Raises:
        HTTPException: If verification fails
    """
    if not settings.COINBASE_PAYMENT_LINK_WEBHOOK_SECRET:
        logger.error(
            "Payment Link webhook received but COINBASE_PAYMENT_LINK_WEBHOOK_SECRET not configured",
            event_type="coinbase_pl_webhook_not_configured",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Coinbase Payment Link webhook not configured",
        )

    sig_header = request.headers.get("x-hook0-signature")
    if not sig_header:
        logger.warning(
            "Coinbase Payment Link webhook missing signature header",
            event_type="coinbase_pl_webhook_missing_signature",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Hook0-Signature header",
        )

    try:
        # Parse signature header: t=timestamp,h=header_names,v1=signature
        elements = {}
        for part in sig_header.split(","):
            key, _, value = part.partition("=")
            elements[key.strip()] = value.strip()

        timestamp = elements.get("t")
        header_names_str = elements.get("h")
        provided_signature = elements.get("v1")

        if not timestamp or not header_names_str or not provided_signature:
            raise ValueError(
                f"Missing required signature fields. Got keys: {list(elements.keys())}"
            )

        # Verify timestamp to prevent replay attacks
        try:
            webhook_time = int(timestamp)
            current_time = int(time.time())
            age_minutes = (current_time - webhook_time) / 60.0

            if age_minutes > MAX_WEBHOOK_AGE_MINUTES:
                logger.warning(
                    "Coinbase Payment Link webhook timestamp too old",
                    age_minutes=round(age_minutes, 1),
                    max_age_minutes=MAX_WEBHOOK_AGE_MINUTES,
                    event_type="coinbase_pl_webhook_replay",
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Webhook timestamp too old (possible replay attack)",
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid timestamp in signature header",
            )

        # Build header values string from the header names listed in h=
        header_name_list = header_names_str.split(" ")
        header_values = ".".join(
            request.headers.get(name, "") for name in header_name_list
        )

        # Build signed payload: "{timestamp}.{header_names}.{header_values}.{body}"
        body_str = body_bytes.decode("utf-8")
        signed_payload = f"{timestamp}.{header_names_str}.{header_values}.{body_str}"

        # Compute expected HMAC-SHA256 signature
        expected_signature = hmac.new(
            settings.COINBASE_PAYMENT_LINK_WEBHOOK_SECRET.encode("utf-8"),
            signed_payload.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()

        # Compare signatures securely
        if not hmac.compare_digest(expected_signature, provided_signature):
            logger.warning(
                "Coinbase Payment Link webhook signature verification failed",
                event_type="coinbase_pl_webhook_invalid_signature",
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid signature",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error verifying Payment Link webhook signature",
            error=str(e),
            event_type="coinbase_pl_webhook_verification_error",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signature verification error",
        )


async def verify_legacy_commerce_signature(
    request: Request, body_bytes: bytes
) -> None:
    """
    Verify the legacy Commerce Charge API webhook signature (X-CC-Webhook-Signature).

    DEPRECATED: This will be removed after migration to Payment Link API is complete.

    Raises:
        HTTPException: If verification fails
    """
    if not settings.COINBASE_COMMERCE_WEBHOOK_SECRET:
        logger.error(
            "Legacy Commerce webhook received but COINBASE_COMMERCE_WEBHOOK_SECRET not configured",
            event_type="coinbase_legacy_webhook_not_configured",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Coinbase Commerce webhook not configured",
        )

    sig_header = request.headers.get("x-cc-webhook-signature")
    if not sig_header:
        logger.warning(
            "Legacy Commerce webhook missing signature header",
            event_type="coinbase_legacy_webhook_missing_signature",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-CC-Webhook-Signature header",
        )

    try:
        signature = hmac.new(
            settings.COINBASE_COMMERCE_WEBHOOK_SECRET.encode("utf-8"),
            body_bytes,
            digestmod=hashlib.sha256,
        ).hexdigest()
    except Exception as e:
        logger.error(
            "Error computing legacy Commerce signature",
            error=str(e),
            event_type="coinbase_legacy_webhook_verification_error",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signature verification error",
        )

    if not hmac.compare_digest(signature, sig_header):
        logger.warning(
            "Legacy Commerce webhook signature verification failed",
            event_type="coinbase_legacy_webhook_invalid_signature",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature",
        )


# === Payment Link API Webhook Handler (New) ===


async def _handle_payment_link_webhook(
    payload: dict,
    db: AsyncSession,
) -> dict:
    """
    Handle a Payment Link API webhook event.

    Payment Link payloads are flat structures with top-level fields:
        {
            "id": "69163c762331ed43dc64a6ef",
            "eventType": "payment_link.payment.success",
            "amount": "100.00",
            "currency": "USDC",
            "status": "COMPLETED",
            "metadata": {...},
            "settlement": {"feeAmount": "1.25", "netAmount": "98.75", "totalAmount": "100.00"},
            ...
        }
    """
    event_type = payload.get("eventType")
    payment_link_id = payload.get("id")

    if not event_type or not payment_link_id:
        logger.warning(
            "Invalid Payment Link webhook payload (missing eventType or id)",
            payload_keys=list(payload.keys()) if isinstance(payload, dict) else None,
            event_type="coinbase_pl_webhook_invalid_format",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid event format: missing eventType or id",
        )

    logger.info(
        "Received Coinbase Payment Link webhook event",
        coinbase_event_type=event_type,
        coinbase_payment_link_id=payment_link_id,
        coinbase_status=payload.get("status"),
    )

    if event_type == coinbase_webhook_service.EVENT_TYPE_PL_PAYMENT_SUCCESS:
        success, message = await coinbase_webhook_service.handle_payment_success(
            db=db,
            payload=payload,
        )
        if not success:
            logger.error(
                "Failed to process payment_link.payment.success",
                coinbase_payment_link_id=payment_link_id,
                error=message,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process event: {message}",
            )

    elif event_type == coinbase_webhook_service.EVENT_TYPE_PL_PAYMENT_FAILED:
        success, message = await coinbase_webhook_service.handle_payment_failed(
            db=db,
            payload=payload,
        )
        if not success:
            logger.warning(
                "Error logging payment_link.payment.failed",
                coinbase_payment_link_id=payment_link_id,
                error=message,
            )
            # Don't raise 500 for failed payments - just log

    elif event_type == coinbase_webhook_service.EVENT_TYPE_PL_PAYMENT_EXPIRED:
        success, message = await coinbase_webhook_service.handle_payment_expired(
            db=db,
            payload=payload,
        )
        if not success:
            logger.warning(
                "Error logging payment_link.payment.expired",
                coinbase_payment_link_id=payment_link_id,
                error=message,
            )
            # Don't raise 500 for expired payments - just log

    else:
        logger.info(
            "Received unhandled Coinbase Payment Link event type",
            coinbase_event_type=event_type,
            coinbase_payment_link_id=payment_link_id,
        )

    return {"received": True}


# === Legacy Commerce Webhook Handler ===


async def _handle_legacy_commerce_webhook(
    payload: dict,
    db: AsyncSession,
) -> dict:
    """
    Handle a legacy Commerce Charge API webhook event.

    DEPRECATED: Will be removed after migration to Payment Link API is complete.

    Legacy payloads wrap events:
        { "id": "delivery-id", "event": { "id": "...", "type": "charge:confirmed", "data": {...} } }
    """
    event = payload.get("event")
    if not event or not isinstance(event, dict):
        logger.warning(
            "Invalid legacy Commerce webhook payload (missing 'event' key)",
            payload_keys=list(payload.keys()) if isinstance(payload, dict) else None,
            event_type="coinbase_legacy_webhook_invalid_format",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid event format: missing 'event' key",
        )

    event_id = event.get("id")
    event_type = event.get("type")

    if not event_id or not event_type:
        logger.warning(
            "Invalid legacy Commerce event format (missing id or type)",
            event_keys=list(event.keys()),
            event_type="coinbase_legacy_webhook_invalid_format",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid event format: missing id or type",
        )

    logger.info(
        "Received legacy Commerce webhook event (consider migrating to Payment Link API)",
        coinbase_event_id=event_id,
        coinbase_event_type=event_type,
    )

    event_data = event.get("data", {})

    # charge:pending = payment detected on chain (fast, ~seconds)
    # charge:confirmed = fully finalized (slow, ~12 min for some chains)
    # Both credit the user; idempotency prevents double-credit if both fire
    if event_type in (
        coinbase_webhook_service.EVENT_TYPE_CHARGE_PENDING,
        coinbase_webhook_service.EVENT_TYPE_CHARGE_CONFIRMED,
    ):
        success, message = await coinbase_webhook_service.handle_charge_confirmed(
            db=db,
            event_data=event_data,
            event_id=event_id,
            event_type=event_type,
        )
        if not success:
            logger.error(
                f"Failed to process {event_type}",
                coinbase_event_id=event_id,
                error=message,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process event: {message}",
            )
    else:
        logger.info(
            "Received unhandled legacy Commerce event type",
            coinbase_event_id=event_id,
            coinbase_event_type=event_type,
        )

    return {"received": True}


# === Main Endpoint ===


@router.post("/coinbase")
async def handle_coinbase_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Handle incoming Coinbase webhook events.

    Supports both Payment Link API (new) and legacy Commerce Charge API formats.
    Auto-detects the format based on the signature header:
    - X-Hook0-Signature  → Payment Link API (payment_link.payment.*)
    - X-CC-Webhook-Signature → Legacy Commerce (charge:*)

    Payment Link event types:
    - payment_link.payment.success: Payment completed successfully
    - payment_link.payment.failed: Payment failed
    - payment_link.payment.expired: Payment link expired

    Legacy event types (deprecated):
    - charge:pending: Payment detected on chain (fast, ~seconds)
    - charge:confirmed: Payment fully finalized (slow, ~12 min for some chains)
    """
    # Read body once for verification and parsing
    try:
        body_bytes = await request.body()
    except Exception as e:
        logger.error(
            "Failed to read webhook body",
            error=str(e),
            event_type="coinbase_webhook_body_error",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request body",
        )

    # Detect webhook format and verify signature
    webhook_format = _detect_webhook_format(request)

    if webhook_format == "payment_link":
        await verify_payment_link_signature(request, body_bytes)
    elif webhook_format == "legacy_commerce":
        await verify_legacy_commerce_signature(request, body_bytes)
    else:
        logger.warning(
            "Coinbase webhook missing both X-Hook0-Signature and X-CC-Webhook-Signature headers",
            event_type="coinbase_webhook_no_signature",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing webhook signature header",
        )

    # Parse JSON body
    try:
        payload = json.loads(body_bytes)
    except Exception as e:
        logger.error(
            "Failed to parse webhook JSON body",
            error=str(e),
            event_type="coinbase_webhook_parse_error",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON body",
        )

    # Route to the appropriate handler
    try:
        if webhook_format == "payment_link":
            return await _handle_payment_link_webhook(payload, db)
        else:
            return await _handle_legacy_commerce_webhook(payload, db)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Error processing Coinbase webhook event",
            webhook_format=webhook_format,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error processing webhook",
        )


# Export router
coinbase_webhook_router = router
