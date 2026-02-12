"""
Coinbase webhook service for processing payment events.

Supports both:
- NEW: Payment Link API events (payment_link.payment.success/failed/expired)
- LEGACY: Commerce Charge API events (charge:confirmed, etc.)

Docs: https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/webhooks
Migration: https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/migrate/overview
"""
from typing import Optional, Tuple, Dict, Any
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CreditLedger, LedgerStatus, LedgerEntryType, User
from src.crud import credits as credits_crud
from src.crud import user as user_crud
from src.core.logging_config import get_core_logger
from src.core.config import settings

logger = get_core_logger()


class CoinbaseWebhookService:
    """
    Service for processing Coinbase webhook events.

    All methods are idempotent - processing the same event multiple times
    will not result in duplicate credits or ledger entries.
    """

    SOURCE_NAME = "coinbase"

    # ── Payment Link API Event Types (New) ───────────────────────────────
    EVENT_TYPE_PL_PAYMENT_SUCCESS = "payment_link.payment.success"
    EVENT_TYPE_PL_PAYMENT_FAILED = "payment_link.payment.failed"
    EVENT_TYPE_PL_PAYMENT_EXPIRED = "payment_link.payment.expired"

    # ── Legacy Commerce Charge API Event Types (Deprecated) ──────────────
    EVENT_TYPE_CHARGE_CONFIRMED = "charge:confirmed"
    EVENT_TYPE_CHARGE_FAILED = "charge:failed"
    EVENT_TYPE_CHARGE_DELAYED = "charge:delayed"
    EVENT_TYPE_CHARGE_PENDING = "charge:pending"
    EVENT_TYPE_CHARGE_RESOLVED = "charge:resolved"

    # Currencies treated as USD-equivalent for crediting purposes
    # USDC is a stablecoin pegged 1:1 to USD
    USD_EQUIVALENT_CURRENCIES = {"USD", "USDC"}

    def __init__(self):
        """Initialize the Coinbase webhook service."""
        pass

    # =====================================================================
    #  Shared Helper Methods
    # =====================================================================

    def _get_idempotency_key(self, event_id: str, event_type: str) -> str:
        """
        Generate an idempotency key for a Coinbase event.

        Args:
            event_id: Coinbase event or payment link ID
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

        # Check by transaction ID
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

        Returns:
            Tuple of (user, error_message). User is None if error.
        """
        log_context = log_context or {}

        cognito_user_id = metadata.get("user_id") if metadata else None

        if not cognito_user_id:
            logger.error(
                "Missing user_id in metadata",
                **log_context,
            )
            return None, "Missing user_id in metadata"

        user = await user_crud.get_user_by_cognito_id(db, cognito_user_id)
        if not user:
            logger.error(
                "User not found",
                cognito_user_id=cognito_user_id,
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
        Create a purchase ledger entry and update balance atomically.

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
            auto_commit=False,
        )

        await credits_crud.update_balance(
            db=db,
            user_id=user_id,
            paid_posted_delta=amount_usd,
            auto_commit=False,
        )

        # Commit both atomically
        await db.commit()
        await db.refresh(entry)

        return entry

    # =====================================================================
    #  Payment Link API Event Handlers (New)
    # =====================================================================

    @staticmethod
    def _validate_payment_link_amount(
        payload: Dict[str, Any],
        log_context: Dict[str, Any],
    ) -> Tuple[Optional[Decimal], Optional[str]]:
        """
        Validate and extract USD-equivalent amount from a Payment Link payload.

        Payment Link payloads have flat structure:
            { "amount": "100.00", "currency": "USDC", ... }

        Settlement breakdown is also available:
            { "settlement": { "totalAmount": "100.00", "netAmount": "98.75", "feeAmount": "1.25" } }

        We credit the user based on the total `amount` field (what the user paid),
        not `settlement.netAmount` (what we receive after fees).

        Returns:
            Tuple of (amount_usd, error_message). amount_usd is None if error.
        """
        currency = payload.get("currency")
        amount_str = payload.get("amount")

        if not currency:
            logger.error(
                "Missing currency in Payment Link payload",
                **log_context,
            )
            return None, "Missing currency information"

        if currency not in CoinbaseWebhookService.USD_EQUIVALENT_CURRENCIES:
            logger.error(
                f"Unsupported currency: {currency} (only USD/USDC accepted)",
                currency=currency,
                **log_context,
            )
            return None, f"Unsupported currency: {currency}"

        if not amount_str:
            logger.error(
                "Missing amount in Payment Link payload",
                **log_context,
            )
            return None, "Missing amount information"

        try:
            amount_usd = Decimal(amount_str)
        except Exception as e:
            logger.error(
                "Invalid amount format in Payment Link payload",
                amount_str=amount_str,
                error=str(e),
                **log_context,
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

    async def handle_payment_success(
        self,
        db: AsyncSession,
        payload: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Handle payment_link.payment.success - payment completed.

        Payment Link success payload (flat structure):
            {
                "id": "69163c762331ed43dc64a6ef",
                "eventType": "payment_link.payment.success",
                "amount": "100.00",
                "currency": "USDC",
                "status": "COMPLETED",
                "network": "base",
                "address": "0x...",
                "tokenAddress": "0x...",
                "metadata": { "user_id": "123", ... },
                "settlement": { "totalAmount": "100.00", "netAmount": "98.75", "feeAmount": "1.25" },
                "url": "https://payments.coinbase.com/payment-links/...",
                "description": "...",
                "createdAt": "...",
                "updatedAt": "..."
            }
        """
        payment_link_id = payload.get("id")
        event_type = self.EVENT_TYPE_PL_PAYMENT_SUCCESS

        log_context = {
            "coinbase_payment_link_id": payment_link_id,
            "coinbase_network": payload.get("network"),
            "coinbase_currency": payload.get("currency"),
        }

        # Idempotency check - use payment_link_id as both event and transaction ID
        # (Payment Link IDs are unique 24-char hex strings)
        existing = await self._check_idempotency(
            db, payment_link_id, event_type, payment_link_id, log_context
        )
        if existing:
            return True, "Already processed"

        # Get user from metadata
        metadata = payload.get("metadata") or {}
        user, error = await self._get_user_from_metadata(
            db, metadata, log_context=log_context
        )
        if error:
            return False, error

        # Validate and extract amount
        amount_usd, error = self._validate_payment_link_amount(payload, log_context)
        if error:
            return False, error

        # Build payment metadata for audit trail
        settlement = payload.get("settlement") or {}
        payment_metadata = {
            "payment_link_id": payment_link_id,
            "url": payload.get("url"),
            "network": payload.get("network"),
            "address": payload.get("address"),
            "token_address": payload.get("tokenAddress"),
            "currency": payload.get("currency"),
            "status": payload.get("status"),
            "settlement_total": settlement.get("totalAmount"),
            "settlement_net": settlement.get("netAmount"),
            "settlement_fee": settlement.get("feeAmount"),
            "description": payload.get("description"),
            "created_at": payload.get("createdAt"),
            "updated_at": payload.get("updatedAt"),
            "expires_at": payload.get("expiresAt"),
            "type": "payment_link",
        }

        # Create entry and update balance
        entry = await self._create_purchase_entry(
            db=db,
            user_id=user.id,
            amount_usd=amount_usd,
            event_id=payment_link_id,
            event_type=event_type,
            transaction_id=payment_link_id,
            payment_metadata=payment_metadata,
            description=f"Coinbase Payment Link - {payload.get('currency', 'USDC')} on {payload.get('network', 'base')}",
        )

        logger.info(
            "Processed Coinbase Payment Link success",
            event_type=event_type,
            user_id=user.id,
            amount_usd=str(amount_usd),
            ledger_entry_id=str(entry.id),
            coinbase_network=payload.get("network"),
            coinbase_settlement_net=settlement.get("netAmount"),
            **log_context,
        )

        return True, "Credits added successfully"

    async def handle_payment_failed(
        self,
        db: AsyncSession,
        payload: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Handle payment_link.payment.failed - payment attempt failed.

        No credits are added. This is logged for monitoring/alerting purposes.
        """
        payment_link_id = payload.get("id")
        metadata = payload.get("metadata") or {}

        logger.warning(
            "Coinbase Payment Link payment failed",
            coinbase_payment_link_id=payment_link_id,
            coinbase_amount=payload.get("amount"),
            coinbase_currency=payload.get("currency"),
            coinbase_network=payload.get("network"),
            coinbase_status=payload.get("status"),
            metadata_user_id=metadata.get("user_id"),
        )

        return True, "Failed payment logged"

    async def handle_payment_expired(
        self,
        db: AsyncSession,
        payload: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Handle payment_link.payment.expired - payment link expired without payment.

        No credits are added. This is logged for monitoring/alerting purposes.
        """
        payment_link_id = payload.get("id")
        metadata = payload.get("metadata") or {}

        logger.info(
            "Coinbase Payment Link expired",
            coinbase_payment_link_id=payment_link_id,
            coinbase_amount=payload.get("amount"),
            coinbase_currency=payload.get("currency"),
            coinbase_expires_at=payload.get("expiresAt"),
            metadata_user_id=metadata.get("user_id"),
        )

        return True, "Expired payment logged"

    # =====================================================================
    #  Legacy Commerce Charge API Handlers (Deprecated)
    # =====================================================================

    @staticmethod
    def _validate_legacy_charge_amount(
        pricing: Dict[str, Any],
        log_context: Dict[str, Any],
    ) -> Tuple[Optional[Decimal], Optional[str]]:
        """
        Validate and extract USD amount from legacy charge pricing data.

        DEPRECATED: Will be removed after migration.
        """
        local_pricing = pricing.get("local") or {}
        currency = local_pricing.get("currency")
        amount_str = local_pricing.get("amount")

        if not currency:
            logger.error(
                "Missing currency information in legacy Coinbase charge",
                **log_context,
            )
            return None, "Missing currency information"

        if currency != "USD":
            logger.error(
                f"Unsupported currency: {currency} (only USD accepted)",
                currency=currency,
                **log_context,
            )
            return None, f"Unsupported currency: {currency}"

        if not amount_str:
            return None, "Missing amount information"

        try:
            amount_usd = Decimal(amount_str)
        except Exception as e:
            logger.error(
                "Invalid amount format",
                amount_str=amount_str,
                error=str(e),
                **log_context,
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

    async def handle_charge_confirmed(
        self,
        db: AsyncSession,
        event_data: Dict[str, Any],
        event_id: str,
        event_type: str,
    ) -> Tuple[bool, str]:
        """
        Handle a legacy charge:confirmed event (payment received).

        DEPRECATED: Will be removed after migration to Payment Link API.
        """
        charge_code = event_data.get("code")
        charge_id = event_data.get("id")
        transaction_id = charge_code

        log_context = {
            "coinbase_charge_code": charge_code,
            "coinbase_charge_id": charge_id,
        }

        # Check for duplicate
        existing = await self._check_idempotency(
            db, event_id, event_type, transaction_id, log_context
        )
        if existing:
            return True, "Already processed"

        # Get user from metadata
        metadata = event_data.get("metadata") or {}
        user, error = await self._get_user_from_metadata(
            db, metadata, log_context=log_context
        )
        if error:
            return False, error

        # Validate amount
        pricing = event_data.get("pricing") or {}
        amount_usd, error = self._validate_legacy_charge_amount(pricing, log_context)
        if error:
            return False, error

        # Build payment metadata
        payment_metadata = {
            "charge_id": charge_id,
            "charge_code": charge_code,
            "hosted_url": event_data.get("hosted_url"),
            "created_at": event_data.get("created_at"),
            "payments": event_data.get("payments", []),
            "type": "charge",
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
            description="Coinbase payment - Charge Confirmed (Legacy)",
        )

        logger.info(
            "Processed legacy Coinbase charge:confirmed",
            event_type=self.EVENT_TYPE_CHARGE_CONFIRMED,
            user_id=user.id,
            amount_usd=str(amount_usd),
            ledger_entry_id=str(entry.id),
            **log_context,
        )

        return True, "Credits added successfully"


# Singleton instance
coinbase_webhook_service = CoinbaseWebhookService()
