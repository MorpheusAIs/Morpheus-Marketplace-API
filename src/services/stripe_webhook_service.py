"""
Stripe webhook service for processing payment events.

Handles:
- checkout.session.completed: One-time payment completions
- invoice.paid: Subscription invoice payments
- invoice.payment_failed: Failed payment attempts
"""
from typing import Optional, Tuple, Dict, Any
from decimal import Decimal
import stripe

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CreditLedger, LedgerStatus, LedgerEntryType, User
from src.crud import credits as credits_crud
from src.crud import user as user_crud
from src.core.logging_config import get_core_logger
from src.core.config import settings

logger = get_core_logger()


class StripeWebhookService:
    """
    Service for processing Stripe webhook events.
    
    All methods are idempotent - processing the same event multiple times
    will not result in duplicate credits or ledger entries.
    """
    
    SOURCE_NAME = "stripe"

    EVENT_TYPE_CHECKOUT_SESSION_COMPLETED = "checkout.session.completed"
    EVENT_TYPE_INVOICE_PAID = "invoice.paid"
    EVENT_TYPE_INVOICE_PAYMENT_FAILED = "invoice.payment_failed"
    
    def __init__(self):
        """Initialize the Stripe webhook service."""
        if settings.STRIPE_SECRET_KEY:
            stripe.api_key = settings.STRIPE_SECRET_KEY
    
    # === Helper Methods ===
    
    def _get_idempotency_key(self, event_id: str, event_type: str) -> str:
        """
        Generate an idempotency key for a Stripe event.
        
        Args:
            event_id: Stripe event ID
            event_type: Stripe event type
        """
        return f"stripe:{event_id}:{event_type}"
    
    async def _check_idempotency(
        self,
        db: AsyncSession,
        event_id: str,
        event_type: str,
        transaction_id: str,
        log_context: Dict[str, Any],
    ) -> Optional[CreditLedger]:
        """
        Check if this event or transaction has already been processed.
        
        Uses two-level duplicate detection per Stripe best practices:
        1. By event ID - guards against same event delivered multiple times
        2. By transaction ID - guards against multiple events for same payment
        
        Args:
            db: Database session
            event_id: Stripe event ID
            event_type: Stripe event type
            transaction_id: The primary transaction ID (checkout_session_id, invoice_id, etc.)
            log_context: Additional context for logging
            
        Returns:
            Existing ledger entry if duplicate, None otherwise
        """
        idempotency_key = self._get_idempotency_key(event_id, event_type)
        
        # Check by Stripe event ID first
        existing_by_event = await credits_crud.get_ledger_entry_by_idempotency_key(
            db, idempotency_key
        )
        if existing_by_event:
            logger.info(
                "Stripe event already processed (by event_id)",
                stripe_event_id=event_id,
                ledger_entry_id=str(existing_by_event.id),
                **log_context,
            )
            return existing_by_event
        
        # Check by transaction ID
        existing_by_txn = await credits_crud.get_ledger_entry_by_external_transaction(
            db, transaction_id, payment_source=self.SOURCE_NAME
        )
        if existing_by_txn:
            logger.info(
                "Transaction already processed (by transaction_id)",
                stripe_event_id=event_id,
                ledger_entry_id=str(existing_by_txn.id),
                **log_context,
            )
            return existing_by_txn
        
        return None
    
    async def _get_user_from_metadata(
        self,
        db: AsyncSession,
        metadata: Dict[str, Any],
        fallback_metadata: Optional[Dict[str, Any]] = None,
        log_context: Dict[str, Any] = None,
    ) -> Tuple[Optional[User], Optional[str]]:
        """
        Extract and validate user from Stripe metadata.
        
        Args:
            db: Database session
            metadata: Primary metadata dict to check
            fallback_metadata: Optional fallback metadata dict
            log_context: Additional context for logging
            
        Returns:
            Tuple of (user, error_message). User is None if error.
        """
        log_context = log_context or {}
        
        # Try primary metadata first
        user_id_str = metadata.get("user_id") if metadata else None
        
        # Fall back to secondary metadata
        if not user_id_str and fallback_metadata:
            user_id_str = fallback_metadata.get("user_id")
        
        if not user_id_str:
            logger.error(
                "Missing user_id in metadata",
                **log_context,
            )
            return None, "Missing user_id in metadata"
        
        try:
            user_id = int(user_id_str)
        except ValueError:
            logger.error(
                "Invalid user_id in metadata",
                user_id_str=user_id_str,
                **log_context,
            )
            return None, "Invalid user_id in metadata"
        
        user = await user_crud.get_user_by_id(db, user_id)
        if not user:
            logger.error(
                "User not found",
                user_id=user_id,
                **log_context,
            )
            return None, "User not found"
        
        return user, None
    
    async def _create_purchase_entry(
        self,
        db: AsyncSession,
        user_id: int,
        amount_usd: Decimal,
        event_id: str,
        event_type: str,
        transaction_id: str,
        payment_metadata: Dict[str, Any],
        description: str,
    ) -> CreditLedger:
        """
        Create a purchase ledger entry and update balance.
        
        Args:
            db: Database session
            user_id: User ID to credit
            amount_usd: Amount in USD to add
            event_id: Stripe event ID for idempotency
            event_type: Stripe event type for idempotency
            transaction_id: Primary transaction ID
            payment_metadata: Provider-specific metadata dict
            description: Human-readable description
            
        Returns:
            Created ledger entry
        """
        idempotency_key = self._get_idempotency_key(event_id, event_type)
        
        entry = await credits_crud.create_ledger_entry(
            db=db,
            user_id=user_id,
            entry_type=LedgerEntryType.purchase,
            status=LedgerStatus.posted,
            amount_paid=amount_usd,
            amount_staking=Decimal("0"),
            idempotency_key=idempotency_key,
            description=description,
            payment_source=self.SOURCE_NAME,
            external_transaction_id=transaction_id,
            payment_metadata=payment_metadata,
        )
        
        await credits_crud.update_balance(
            db=db,
            user_id=user_id,
            paid_posted_delta=amount_usd,
        )
        
        return entry
    
    @staticmethod
    def _validate_amount(amount_cents: int, log_context: Dict[str, Any]) -> Tuple[Optional[Decimal], Optional[str]]:
        """
        Validate and convert Stripe amount (cents) to USD.
        
        Args:
            amount_cents: Amount in cents from Stripe
            log_context: Context for logging
            
        Returns:
            Tuple of (amount_usd, error_message). amount_usd is None if error.
        """
        amount_usd = Decimal(amount_cents) / 100
        
        if amount_usd <= 0:
            logger.warning(
                "Invalid payment amount (zero or negative)",
                amount_cents=amount_cents,
                **log_context,
            )
            return None, "Invalid payment amount"
        
        return amount_usd, None
    
    # === Event Handlers ===
    
    async def handle_checkout_session_completed(
        self,
        db: AsyncSession,
        session: stripe.checkout.Session,
        event_id: str,
        event_type: str,
    ) -> Tuple[bool, str]:
        """
        Handle a completed checkout session.
        
        This is triggered when a customer successfully completes a one-time
        payment through Stripe Checkout.
        """
        session_id = session.id
        log_context = {"stripe_checkout_session_id": session_id}
        
        # Check for duplicate
        existing = await self._check_idempotency(
            db, event_id, event_type, session_id, log_context
        )
        if existing:
            return True, "Already processed"
        
        # Get user from metadata
        metadata = session.metadata or {}
        user, error = await self._get_user_from_metadata(db, metadata, log_context=log_context)
        if error:
            return False, error
        
        # Validate amount
        amount_usd, error = self._validate_amount(session.amount_total or 0, log_context)
        if error:
            return False, error
        
        # Build payment metadata
        payment_metadata = {
            "checkout_session_id": session_id,
            "type": "checkout",
        }
        if session.payment_intent and isinstance(session.payment_intent, str):
            payment_metadata["payment_intent_id"] = session.payment_intent
        if session.customer and isinstance(session.customer, str):
            payment_metadata["customer_id"] = session.customer
        
        # Create entry and update balance
        entry = await self._create_purchase_entry(
            db=db,
            user_id=user.id,
            amount_usd=amount_usd,
            event_id=event_id,
            event_type=event_type,
            transaction_id=session_id,
            payment_metadata=payment_metadata,
            description="Stripe payment - Checkout Session",
        )
        
        logger.info(
            "Processed Stripe webhook event",
            event_type=self.EVENT_TYPE_CHECKOUT_SESSION_COMPLETED,
            user_id=user.id,
            amount_usd=str(amount_usd),
            ledger_entry_id=str(entry.id),
            **log_context,
        )
        
        return True, "Credits added successfully"
    
    async def handle_invoice_paid(
        self,
        db: AsyncSession,
        invoice: stripe.Invoice,
        event_id: str,
        event_type: str,
    ) -> Tuple[bool, str]:
        """
        Handle a paid invoice event.
        
        This is triggered when an invoice is successfully paid, typically
        for subscription payments or invoice-based billing.
        """
        invoice_id = invoice.id
        log_context = {"stripe_invoice_id": invoice_id}
        
        # Check for duplicate
        existing = await self._check_idempotency(
            db, event_id, event_type, invoice_id, log_context
        )
        if existing:
            return True, "Already processed"
        
        # Get user from metadata (try invoice first, then subscription)
        metadata = invoice.metadata or {}
        fallback_metadata = None
        
        if not metadata.get("user_id") and invoice.get("subscription"):
            try:
                subscription = stripe.Subscription.retrieve(invoice.subscription)
                fallback_metadata = subscription.metadata or {}
            except stripe.error.StripeError as e:
                logger.warning(
                    "Failed to retrieve subscription metadata",
                    stripe_subscription_id=invoice.subscription,
                    error=str(e),
                    **log_context,
                )
        
        user, error = await self._get_user_from_metadata(
            db, metadata, fallback_metadata, log_context
        )
        if error:
            return False, error
        
        # Validate amount
        amount_usd, error = self._validate_amount(invoice.amount_paid or 0, log_context)
        if error:
            return False, error
        
        # Build payment metadata
        payment_metadata = {
            "invoice_id": invoice_id,
            "type": "invoice",
        }
        if invoice.get("payment_intent") and isinstance(invoice.get("payment_intent"), str):
            payment_metadata["payment_intent_id"] = invoice.payment_intent
        if invoice.get("customer") and isinstance(invoice.get("customer"), str):
            payment_metadata["customer_id"] = invoice.customer
        if invoice.get("subscription") and isinstance(invoice.get("subscription"), str):
            payment_metadata["subscription_id"] = invoice.subscription
        
        # Create entry and update balance
        entry = await self._create_purchase_entry(
            db=db,
            user_id=user.id,
            amount_usd=amount_usd,
            event_id=event_id,
            event_type=event_type,
            transaction_id=invoice_id,
            payment_metadata=payment_metadata,
            description="Stripe payment - Invoice",
        )
        
        logger.info(
            "Processed Stripe webhook event",
            event_type=self.EVENT_TYPE_INVOICE_PAID,
            user_id=user.id,
            amount_usd=str(amount_usd),
            ledger_entry_id=str(entry.id),
            **log_context,
        )
        
        return True, "Credits added successfully"
    
    async def handle_invoice_payment_failed(
        self,
        db: AsyncSession,
        invoice: stripe.Invoice,
    ) -> Tuple[bool, str]:
        """
        Handle a failed invoice payment event.
        
        This is triggered when a payment attempt on an invoice fails.
        We log this event but don't modify credits (since no payment was made).
        """
        invoice_id = invoice.id
        
        # Extract user_id from metadata for logging
        metadata = invoice.metadata or {}
        user_id_str = metadata.get("user_id")
        
        if not user_id_str and invoice.subscription:
            try:
                subscription = stripe.Subscription.retrieve(invoice.subscription)
                user_id_str = (subscription.metadata or {}).get("user_id")
            except stripe.error.StripeError as e:
                logger.warning(
                    "Failed to retrieve subscription metadata for failed payment",
                    stripe_invoice_id=invoice_id,
                    error=str(e),
                )
        
        logger.warning(
            "Stripe webhook event failed",
            event_type=self.EVENT_TYPE_INVOICE_PAYMENT_FAILED,
            stripe_invoice_id=invoice_id,
            stripe_customer_id=invoice.customer,
            user_id=user_id_str,
            attempt_count=invoice.attempt_count or 0,
            next_payment_attempt=invoice.next_payment_attempt,
            amount_due=invoice.amount_due,
        )
        
        # No ledger entry for failures - no money moved
        return True, "Payment failure logged"


# Singleton instance
stripe_webhook_service = StripeWebhookService()
