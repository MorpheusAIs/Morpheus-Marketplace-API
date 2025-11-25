"""
API Credits Purchase Endpoints

POST /api/v1/credits/purchase/stripe - Create Stripe payment intent
POST /api/v1/credits/purchase/coinbase - Create Coinbase payment
POST /api/v1/credits/purchase/mor - Purchase with MOR tokens
POST /api/v1/credits/webhook/stripe - Stripe webhook handler
POST /api/v1/credits/webhook/coinbase - Coinbase webhook handler
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from decimal import Decimal
from datetime import datetime
import uuid

from ....db.database import get_db
from ....dependencies import get_current_user
from ....db.models import User
from ....crud import api_credits
from ....db.billing_models import (
    PaymentIntent,
    PaymentProvider,
    PaymentStatus,
    CreditTransactionType
)
from ....schemas.credits import (
    StripePurchaseRequest,
    StripePurchaseResponse,
    CoinbasePurchaseRequest,
    CoinbasePurchaseResponse,
    MORPurchaseRequest,
    MORPurchaseResponse
)
from ....services.payment_service import (
    stripe_service,
    coinbase_service,
    mor_payment_service
)
from ....core.logging_config import get_api_logger

logger = get_api_logger()
router = APIRouter()


@router.post("/purchase/stripe", response_model=StripePurchaseResponse)
async def create_stripe_payment(
    request: StripePurchaseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a Stripe payment intent for purchasing API credits.

    Request body:
        - amount_usd: Amount in USD to purchase (min $5)
        - payment_method_id: Stripe payment method ID (optional for saved cards)

    Returns:
        - payment_intent_id: Stripe payment intent ID
        - client_secret: Client secret for confirming payment
        - credits_amount: Amount of credits to be added
    """
    if request.amount_usd < 5:
        raise HTTPException(
            status_code=400,
            detail="Minimum purchase amount is $5 USD"
        )

    # Credits are 1:1 with USD
    credits_amount = Decimal(str(request.amount_usd))

    try:
        # Create Stripe payment intent
        payment_intent = await stripe_service.create_payment_intent(
            amount_usd=request.amount_usd,
            user_id=current_user.id,
            user_email=current_user.email,
            payment_method_id=request.payment_method_id
        )

        # Store payment intent in database
        db_payment_intent = PaymentIntent(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            provider=PaymentProvider.stripe,
            external_id=payment_intent["id"],
            amount_usd=Decimal(str(request.amount_usd)),
            credits_amount=credits_amount,
            status=PaymentStatus.pending,
            currency="USD",
            payment_method="card",
            metadata={"payment_method_id": request.payment_method_id}
        )

        db.add(db_payment_intent)
        await db.commit()

        logger.info("Created Stripe payment intent",
                   user_id=current_user.id,
                   amount_usd=request.amount_usd,
                   payment_intent_id=payment_intent["id"],
                   event_type="stripe_payment_created")

        return StripePurchaseResponse(
            payment_intent_id=payment_intent["id"],
            client_secret=payment_intent["client_secret"],
            credits_amount=credits_amount,
            amount_usd=Decimal(str(request.amount_usd))
        )

    except Exception as e:
        logger.error("Error creating Stripe payment intent",
                    user_id=current_user.id,
                    error=str(e),
                    event_type="stripe_payment_error")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create payment: {str(e)}"
        )


@router.post("/purchase/coinbase", response_model=CoinbasePurchaseResponse)
async def create_coinbase_payment(
    request: CoinbasePurchaseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a Coinbase Commerce charge for purchasing API credits.

    Request body:
        - amount_usd: Amount in USD to purchase (min $5)
        - crypto_currency: Cryptocurrency to use (BTC, ETH, USDC, etc.)

    Returns:
        - charge_id: Coinbase charge ID
        - hosted_url: URL to complete payment
        - credits_amount: Amount of credits to be added
    """
    if request.amount_usd < 5:
        raise HTTPException(
            status_code=400,
            detail="Minimum purchase amount is $5 USD"
        )

    credits_amount = Decimal(str(request.amount_usd))

    try:
        # Create Coinbase Commerce charge
        charge = await coinbase_service.create_charge(
            amount_usd=request.amount_usd,
            user_id=current_user.id,
            user_email=current_user.email,
            crypto_currency=request.crypto_currency
        )

        # Store payment intent in database
        db_payment_intent = PaymentIntent(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            provider=PaymentProvider.coinbase,
            external_id=charge["id"],
            amount_usd=Decimal(str(request.amount_usd)),
            credits_amount=credits_amount,
            status=PaymentStatus.pending,
            currency="USD",
            payment_method="crypto",
            metadata={
                "crypto_currency": request.crypto_currency,
                "hosted_url": charge["hosted_url"]
            }
        )

        db.add(db_payment_intent)
        await db.commit()

        logger.info("Created Coinbase Commerce charge",
                   user_id=current_user.id,
                   amount_usd=request.amount_usd,
                   charge_id=charge["id"],
                   event_type="coinbase_payment_created")

        return CoinbasePurchaseResponse(
            charge_id=charge["id"],
            hosted_url=charge["hosted_url"],
            credits_amount=credits_amount,
            amount_usd=Decimal(str(request.amount_usd))
        )

    except Exception as e:
        logger.error("Error creating Coinbase charge",
                    user_id=current_user.id,
                    error=str(e),
                    event_type="coinbase_payment_error")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create payment: {str(e)}"
        )


@router.post("/purchase/mor", response_model=MORPurchaseResponse)
async def purchase_with_mor(
    request: MORPurchaseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Purchase API credits with MOR tokens.

    Request body:
        - mor_amount: Amount of MOR to spend
        - transaction_hash: Transaction hash of MOR transfer

    Returns:
        - credits_amount: Amount of credits added
        - mor_price_usd: Price of MOR in USD
    """
    try:
        # Verify MOR transaction
        verification = await mor_payment_service.verify_transaction(
            transaction_hash=request.transaction_hash,
            expected_amount=request.mor_amount,
            sender_address=current_user.wallet_address  # Assumes user has wallet
        )

        if not verification["valid"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid MOR transaction"
            )

        # Get MOR price in USD
        mor_price_usd = await mor_payment_service.get_mor_price_usd()
        credits_amount = Decimal(str(request.mor_amount)) * Decimal(str(mor_price_usd))

        # Add credits
        transaction = await api_credits.add_credits(
            db=db,
            user_id=current_user.id,
            amount=credits_amount,
            transaction_type=CreditTransactionType.purchase_mor,
            payment_method="mor",
            payment_id=request.transaction_hash,
            payment_metadata={
                "mor_amount": str(request.mor_amount),
                "mor_price_usd": str(mor_price_usd)
            },
            description=f"Purchased with {request.mor_amount} MOR"
        )

        logger.info("Purchased credits with MOR",
                   user_id=current_user.id,
                   mor_amount=request.mor_amount,
                   credits_amount=str(credits_amount),
                   event_type="mor_purchase_complete")

        return MORPurchaseResponse(
            credits_amount=credits_amount,
            mor_amount=Decimal(str(request.mor_amount)),
            mor_price_usd=Decimal(str(mor_price_usd)),
            transaction_hash=request.transaction_hash
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error purchasing with MOR",
                    user_id=current_user.id,
                    error=str(e),
                    event_type="mor_purchase_error")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to purchase with MOR: {str(e)}"
        )


@router.post("/webhook/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Handle Stripe webhook events.

    Events handled:
        - payment_intent.succeeded
        - payment_intent.payment_failed
    """
    payload = await request.body()

    try:
        event = await stripe_service.verify_webhook(
            payload=payload,
            signature=stripe_signature
        )

        if event["type"] == "payment_intent.succeeded":
            payment_intent_id = event["data"]["object"]["id"]

            # Find payment intent in database
            result = await db.execute(
                select(PaymentIntent).where(
                    PaymentIntent.external_id == payment_intent_id
                )
            )
            db_payment = result.scalar_one_or_none()

            if not db_payment:
                logger.warning("Payment intent not found in database",
                             payment_intent_id=payment_intent_id,
                             event_type="stripe_webhook_payment_not_found")
                return {"status": "ignored"}

            # Add credits
            await api_credits.add_credits(
                db=db,
                user_id=db_payment.user_id,
                amount=db_payment.credits_amount,
                transaction_type=CreditTransactionType.purchase_usd,
                payment_method="stripe",
                payment_id=payment_intent_id,
                description=f"Stripe purchase: ${db_payment.amount_usd}"
            )

            # Update payment status
            db_payment.status = PaymentStatus.succeeded
            db_payment.completed_at = datetime.utcnow()
            await db.commit()

            logger.info("Stripe payment succeeded",
                       user_id=db_payment.user_id,
                       payment_intent_id=payment_intent_id,
                       credits_amount=str(db_payment.credits_amount),
                       event_type="stripe_payment_succeeded")

        elif event["type"] == "payment_intent.payment_failed":
            payment_intent_id = event["data"]["object"]["id"]

            result = await db.execute(
                select(PaymentIntent).where(
                    PaymentIntent.external_id == payment_intent_id
                )
            )
            db_payment = result.scalar_one_or_none()

            if db_payment:
                db_payment.status = PaymentStatus.failed
                await db.commit()

                logger.warning("Stripe payment failed",
                             user_id=db_payment.user_id,
                             payment_intent_id=payment_intent_id,
                             event_type="stripe_payment_failed")

        return {"status": "success"}

    except Exception as e:
        logger.error("Error processing Stripe webhook",
                    error=str(e),
                    event_type="stripe_webhook_error")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/webhook/coinbase")
async def coinbase_webhook(
    request: Request,
    x_cc_webhook_signature: str = Header(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Handle Coinbase Commerce webhook events.

    Events handled:
        - charge:confirmed
        - charge:failed
    """
    payload = await request.body()

    try:
        event = await coinbase_service.verify_webhook(
            payload=payload,
            signature=x_cc_webhook_signature
        )

        if event["type"] == "charge:confirmed":
            charge_id = event["data"]["id"]

            # Find payment intent in database
            result = await db.execute(
                select(PaymentIntent).where(
                    PaymentIntent.external_id == charge_id
                )
            )
            db_payment = result.scalar_one_or_none()

            if not db_payment:
                logger.warning("Charge not found in database",
                             charge_id=charge_id,
                             event_type="coinbase_webhook_charge_not_found")
                return {"status": "ignored"}

            # Add credits
            await api_credits.add_credits(
                db=db,
                user_id=db_payment.user_id,
                amount=db_payment.credits_amount,
                transaction_type=CreditTransactionType.purchase_crypto,
                payment_method="coinbase",
                payment_id=charge_id,
                description=f"Coinbase purchase: ${db_payment.amount_usd}"
            )

            # Update payment status
            db_payment.status = PaymentStatus.succeeded
            db_payment.completed_at = datetime.utcnow()
            await db.commit()

            logger.info("Coinbase payment confirmed",
                       user_id=db_payment.user_id,
                       charge_id=charge_id,
                       credits_amount=str(db_payment.credits_amount),
                       event_type="coinbase_payment_confirmed")

        elif event["type"] == "charge:failed":
            charge_id = event["data"]["id"]

            result = await db.execute(
                select(PaymentIntent).where(
                    PaymentIntent.external_id == charge_id
                )
            )
            db_payment = result.scalar_one_or_none()

            if db_payment:
                db_payment.status = PaymentStatus.failed
                await db.commit()

                logger.warning("Coinbase payment failed",
                             user_id=db_payment.user_id,
                             charge_id=charge_id,
                             event_type="coinbase_payment_failed")

        return {"status": "success"}

    except Exception as e:
        logger.error("Error processing Coinbase webhook",
                    error=str(e),
                    event_type="coinbase_webhook_error")
        raise HTTPException(status_code=400, detail=str(e))
