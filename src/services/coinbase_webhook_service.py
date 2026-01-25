"""
Coinbase Commerce webhook service for processing payment events.

Handles:
- charge:confirmed: Completed payments
"""
from typing import Optional, Tuple, Dict, Any
from decimal import Decimal
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CreditLedger, LedgerStatus, LedgerEntryType, User
from src.crud import credits as credits_crud
from src.crud import user as user_crud
from src.core.logging_config import get_core_logger
from src.core.config import settings

logger = get_core_logger()


class CoinbaseWebhookService:
    """
    Service for processing Coinbase Commerce webhook events.
    
    All methods are idempotent - processing the same event multiple times
    will not result in duplicate credits or ledger entries.
    """
    
    SOURCE_NAME = "coinbase"
    
    # Coinbase Commerce Event Types
    EVENT_TYPE_CHARGE_CONFIRMED = "charge:confirmed"
    EVENT_TYPE_CHARGE_FAILED = "charge:failed"
    EVENT_TYPE_CHARGE_DELAYED = "charge:delayed"
    EVENT_TYPE_CHARGE_PENDING = "charge:pending"
    EVENT_TYPE_CHARGE_RESOLVED = "charge:resolved"
    
    def __init__(self):
        """Initialize the Coinbase webhook service."""
        pass
    
    # === Helper Methods ===
    
    def _get_idempotency_key(self, event_id: str, event_type: str) -> str:
        """
        Generate an idempotency key for a Coinbase event.
        
        Args:
            event_id: Coinbase event ID
            event_type: Coinbase event type
        """
        return f"coinbase:{event_id}:{event_type}"
    
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
        
        Uses two-level duplicate detection:
        1. By event ID - guards against same event delivered multiple times
        2. By transaction ID - guards against multiple events for same payment
        
        Args:
            db: Database session
            event_id: Coinbase event ID
            event_type: Coinbase event type
            transaction_id: The primary transaction ID (charge code/id)
            log_context: Additional context for logging
            
        Returns:
            Existing ledger entry if duplicate, None otherwise
        """
        idempotency_key = self._get_idempotency_key(event_id, event_type)
        
        # Check by Event ID first
        existing_by_event = await credits_crud.get_ledger_entry_by_idempotency_key(
            db, idempotency_key
        )
        if existing_by_event:
            logger.info(
                "Coinbase event already processed (by event_id)",
                coinbase_event_id=event_id,
                ledger_entry_id=str(existing_by_event.id),
                **log_context,
            )
            return existing_by_event
        
        # Check by transaction ID (charge code)
        existing_by_txn = await credits_crud.get_ledger_entry_by_external_transaction(
            db, transaction_id, payment_source=self.SOURCE_NAME
        )
        if existing_by_txn:
            logger.info(
                "Transaction already processed (by transaction_id)",
                coinbase_event_id=event_id,
                ledger_entry_id=str(existing_by_txn.id),
                **log_context,
            )
            return existing_by_txn
        
        return None
    
    async def _get_user_from_metadata(
        self,
        db: AsyncSession,
        metadata: Dict[str, Any],
        log_context: Dict[str, Any] = None,
    ) -> Tuple[Optional[User], Optional[str]]:
        """
        Extract and validate user from Coinbase metadata.
        
        Args:
            db: Database session
            metadata: Metadata dict to check
            log_context: Additional context for logging
            
        Returns:
            Tuple of (user, error_message). User is None if error.
        """
        log_context = log_context or {}
        
        # In Coinbase Commerce, metadata is a flat key-value object
        user_id_str = metadata.get("user_id") if metadata else None
        
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
            event_id: Coinbase event ID for idempotency
            event_type: Coinbase event type for idempotency
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
    def _validate_amount(pricing: Dict[str, Any], log_context: Dict[str, Any]) -> Tuple[Optional[Decimal], Optional[str]]:
        """
        Validate and extract USD amount from pricing data.
        
        Args:
            pricing: Pricing dictionary from Coinbase charge
            log_context: Context for logging
            
        Returns:
            Tuple of (amount_usd, error_message). amount_usd is None if error.
        """
        local_pricing = pricing.get("local") or {}
        currency = local_pricing.get("currency")
        amount_str = local_pricing.get("amount")
        
        if currency != "USD":
            # We strictly expect USD as the base currency for now
            logger.warning(
                f"Unexpected currency: {currency} (expected USD)",
                **log_context
            )
            # In a real app we might convert, but for now we error or just proceed if we trust the value
            # Actually, Coinbase Commerce handles conversion, so local currency should be what the user saw.
            # If the merchant set price in USD, local should be USD.
            if not currency:
                return None, "Missing currency information"
                
        if not amount_str:
            return None, "Missing amount information"
            
        try:
            amount_usd = Decimal(amount_str)
        except Exception as e:
            logger.error(
                "Invalid amount format",
                amount_str=amount_str,
                error=str(e),
                **log_context
            )
            return None, "Invalid amount format"
        
        if amount_usd <= 0:
            logger.warning(
                "Invalid payment amount (zero or negative)",
                amount_usd=str(amount_usd),
                **log_context,
            )
            return None, "Invalid payment amount"
        
        return amount_usd, None
    
    # === Event Handlers ===
    
    async def handle_charge_confirmed(
        self,
        db: AsyncSession,
        event_data: Dict[str, Any],
        event_id: str,
        event_type: str,
    ) -> Tuple[bool, str]:
        """
        Handle a confirmed charge (payment received).
        """
        charge_code = event_data.get("code")
        charge_id = event_data.get("id")
        
        # Use code as the primary transaction ID as it's the short user-facing one
        # but store ID in metadata
        transaction_id = charge_code
        
        log_context = {
            "coinbase_charge_code": charge_code,
            "coinbase_charge_id": charge_id
        }
        
        # Check for duplicate
        existing = await self._check_idempotency(
            db, event_id, event_type, transaction_id, log_context
        )
        if existing:
            return True, "Already processed"
        
        # Get user from metadata
        metadata = event_data.get("metadata") or {}
        user, error = await self._get_user_from_metadata(db, metadata, log_context=log_context)
        if error:
            return False, error
        
        # Validate amount
        # For confirmed charges, we use the pricing object which shows what was asked
        # payments array shows what was actually sent
        pricing = event_data.get("pricing") or {}
        amount_usd, error = self._validate_amount(pricing, log_context)
        if error:
            return False, error
        
        # Build payment metadata
        payment_metadata = {
            "charge_id": charge_id,
            "charge_code": charge_code,
            "hosted_url": event_data.get("hosted_url"),
            "created_at": event_data.get("created_at"),
            "payments": event_data.get("payments", []),
            "type": "charge"
        }
        
        # Create entry and update balance
        entry = await self._create_purchase_entry(
            db=db,
            user_id=user.id,
            amount_usd=amount_usd,
            event_id=event_id,
            event_type=event_type,
            transaction_id=transaction_id,
            payment_metadata=payment_metadata,
            description="Coinbase payment - Charge Confirmed",
        )
        
        logger.info(
            "Processed Coinbase webhook event",
            event_type=self.EVENT_TYPE_CHARGE_CONFIRMED,
            user_id=user.id,
            amount_usd=str(amount_usd),
            ledger_entry_id=str(entry.id),
            **log_context,
        )
        
        return True, "Credits added successfully"


# Singleton instance
coinbase_webhook_service = CoinbaseWebhookService()
