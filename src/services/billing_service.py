"""
Billing service layer for credit operations.
Implements business logic for holds, finalization, voiding, staking refresh, and refunds.
"""
from typing import Optional, Tuple
from datetime import datetime, date
from decimal import Decimal
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CreditLedger, CreditAccountBalance, LedgerStatus, LedgerEntryType
from src.crud import credits as credits_crud
from src.schemas.billing import (
    UsageHoldRequest, UsageHoldResponse,
    UsageFinalizeRequest, UsageFinalizeResponse,
    UsageVoidRequest, UsageVoidResponse,
    RefundRequest, RefundResponse,
    BalanceResponse, PaidBalanceInfo, StakingBalanceInfo,
    StakingRefreshResponse,
)
from src.services.pricing import get_pricing_service, PricingService
from src.core.logging_config import get_core_logger

logger = get_core_logger()


class BillingService:
    """
    Service layer for credit/billing operations.
    All methods requiring atomicity perform ledger + cache updates in a single transaction.
    
    Use billing_service.pricing for cost calculations:
        cost = await billing_service.pricing.calculate_cost(...)
        estimate = await billing_service.pricing.estimate_usage(...)
    """
    
    def __init__(self, pricing_service: Optional[PricingService] = None):
        """
        Initialize the billing service.
        
        Args:
            pricing_service: Optional pricing service instance. Defaults to global singleton.
        """
        self.pricing = pricing_service or get_pricing_service()
    
    # === Balance Operations ===
    
    async def get_balance(self, db: AsyncSession, user_id: int, client_ip: Optional[str] = None) -> BalanceResponse:
        """
        Get the current balance for an account.
        """
        balance = await credits_crud.get_or_create_balance(db, user_id, client_ip=client_ip)
        
        paid_info = PaidBalanceInfo(
            posted_balance=balance.paid_posted_balance or Decimal("0"),
            pending_holds=balance.paid_pending_holds or Decimal("0"),
            available=balance.paid_available,
        )
        
        staking_info = StakingBalanceInfo(
            daily_amount=balance.staking_daily_amount or Decimal("0"),
            refresh_date=balance.staking_refresh_date,
            available=balance.staking_available or Decimal("0"),
        )
        
        return BalanceResponse(
            paid=paid_info,
            staking=staking_info,
            total_available=balance.total_available,
            is_staker=balance.is_staker,
            allow_overage=balance.allow_overage,
            currency="USD",
        )
    
    async def reconcile_balance(self, db: AsyncSession, user_id: int) -> BalanceResponse:
        """
        Reconcile the cached balance against the ledger (source of truth).
        Fixes drift in paid_pending_holds caused by partial transaction failures.
        
        Returns the corrected balance.
        """
        balance = await credits_crud.reconcile_all_balances(db, user_id)
        
        paid_info = PaidBalanceInfo(
            posted_balance=balance.paid_posted_balance or Decimal("0"),
            pending_holds=balance.paid_pending_holds or Decimal("0"),
            available=balance.paid_available,
        )
        
        staking_info = StakingBalanceInfo(
            daily_amount=balance.staking_daily_amount or Decimal("0"),
            refresh_date=balance.staking_refresh_date,
            available=balance.staking_available or Decimal("0"),
        )
        
        return BalanceResponse(
            paid=paid_info,
            staking=staking_info,
            total_available=balance.total_available,
            is_staker=balance.is_staker,
            allow_overage=balance.allow_overage,
            currency="USD",
        )
    
    # === Staking Operations ===
    
    async def refresh_staking(
        self, 
        db: AsyncSession, 
        user_id: int
    ) -> StakingRefreshResponse:
        """
        Perform idempotent daily staking refresh.
        Only refreshes if not already refreshed today.
        """
        today = date.today()
        balance = await credits_crud.get_or_create_balance(db, user_id)
        
        # Check if already refreshed today
        if balance.staking_refresh_date == today:
            logger.info(
                "Staking already refreshed today",
                user_id=user_id,
                refresh_date=str(today),
            )
            return StakingRefreshResponse(
                credits_added=Decimal("0"),
                new_balance=StakingBalanceInfo(
                    daily_amount=balance.staking_daily_amount or Decimal("0"),
                    refresh_date=balance.staking_refresh_date,
                    available=balance.staking_available or Decimal("0"),
                ),
                already_refreshed=True,
                message="Staking already refreshed today",
            )
        
        # Create idempotency key for today's refresh
        idempotency_key = f"staking:{user_id}:{today.isoformat()}"
        
        # Check if ledger entry already exists (double-check idempotency)
        existing_entry = await credits_crud.get_ledger_entry_by_idempotency_key(db, idempotency_key)
        if existing_entry:
            logger.info(
                "Staking refresh ledger entry already exists",
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
            return StakingRefreshResponse(
                credits_added=Decimal("0"),
                new_balance=StakingBalanceInfo(
                    daily_amount=balance.staking_daily_amount or Decimal("0"),
                    refresh_date=balance.staking_refresh_date,
                    available=balance.staking_available or Decimal("0"),
                ),
                already_refreshed=True,
                message="Staking already refreshed today",
            )
        
        # Perform refresh
        daily_amount = balance.staking_daily_amount or Decimal("0")
        
        # Create ledger entry for staking refresh
        await credits_crud.create_ledger_entry(
            db=db,
            user_id=user_id,
            entry_type=LedgerEntryType.staking_refresh,
            status=LedgerStatus.posted,
            idempotency_key=idempotency_key,
            amount_paid=Decimal("0"),
            amount_staking=daily_amount,  # Positive for credit
            description=f"Daily staking refresh for {today.isoformat()}",
        )
        
        # Update balance cache - reset staking to daily amount
        # Note: This resets (doesn't accumulate) per requirement that staking doesn't roll over
        balance.staking_available = daily_amount
        balance.staking_refresh_date = today
        balance.updated_at = datetime.utcnow()
        
        await db.commit()
        await db.refresh(balance)
        
        logger.info(
            "Staking refreshed successfully",
            user_id=user_id,
            daily_amount=str(daily_amount),
            refresh_date=str(today),
        )
        
        return StakingRefreshResponse(
            credits_added=daily_amount,
            new_balance=StakingBalanceInfo(
                daily_amount=balance.staking_daily_amount or Decimal("0"),
                refresh_date=balance.staking_refresh_date,
                available=balance.staking_available or Decimal("0"),
            ),
            already_refreshed=False,
            message="Staking refreshed successfully",
        )
    
    # === Spending Split Logic ===
    
    @staticmethod
    def _compute_spending_split(
        balance: "CreditAccountBalance",
        amount: Decimal,
        staking_reserved: Decimal = Decimal("0"),
    ) -> Tuple[Decimal, Decimal, Decimal]:
        """
        Determine how to split a charge/hold between staking and paid buckets,
        and what the effective available balance is for sufficiency checks.
        
        Rules:
            - No staking at all (not is_staker AND no daily amount): entire amount from paid.
            - Has staking with allow_overage: staking first, paid for remainder.
            - Has staking without allow_overage: staking only, nothing from paid.
        
        Args:
            balance: The current cached balance row.
            amount: The total amount to split.
            staking_reserved: Staking credits already held for THIS transaction
                (e.g. from a pending hold being finalized). These are added back
                to staking_available so the split sees the full pot that belongs
                to this request.
        
        Returns:
            (available, staking_amount, paid_amount)
            - available: effective balance to check sufficiency against
            - staking_amount: portion of `amount` to debit from staking
            - paid_amount: portion of `amount` to debit from paid
        """
        is_staker = balance.is_staker
        has_staking = (balance.staking_daily_amount or Decimal("0")) > 0
        staking_available = (balance.staking_available or Decimal("0")) + staking_reserved
        
        if not is_staker and not has_staking:
            # No staking credits at all — use paid balance only
            return balance.paid_available, Decimal("0"), amount
        
        if balance.allow_overage:
            # Staking first, paid covers the rest
            staking_part = min(amount, staking_available)
            paid_part = amount - staking_part
            return balance.total_available + staking_reserved, staking_part, paid_part
        
        # Staking only — no paid fallback
        return staking_available, min(amount, staking_available), Decimal("0")
    
    # === Usage Hold/Finalize/Void Operations ===
    
    async def create_usage_hold(
        self,
        db: AsyncSession,
        user_id: int,
        request: UsageHoldRequest,
    ) -> UsageHoldResponse:
        """
        Create a usage hold at the start of a request.
        
        This method:
        1. Estimates cost using pricing service
        2. Checks if user has sufficient balance
        3. Creates a hold if balance is sufficient
        
        The ledger entry ID is provided by the caller for easy tracking.
        Returns UsageHoldResponse with success=False and error if insufficient balance.
        """
        # Check for existing hold (idempotency by entry ID)
        existing = await credits_crud.get_ledger_entry_by_id(db, request.ledger_entry_id)
        if existing:
            logger.info(
                "Usage hold already exists",
                user_id=user_id,
                request_id=request.request_id,
                ledger_entry_id=str(request.ledger_entry_id),
            )
            return UsageHoldResponse(
                ledger_entry_id=existing.id,
                hold_amount=existing.amount_paid,
                success=True,
            )
        
        # Estimate cost using pricing service
        estimate = await self.pricing.estimate_usage(
            estimated_input_tokens=request.estimated_input_tokens,
            estimated_output_tokens=request.estimated_output_tokens,
            model_name=request.model_name,
            model_id=request.model_id,
            db=db,
        )
        
        estimated_cost = estimate.estimated_total_cost
        
        # FOR UPDATE: lock the balance row to prevent concurrent workers from
        # both passing the sufficiency check and creating holds that exceed
        # available funds.  The lock is held until the commit at the end.
        balance = await credits_crud.get_or_create_balance(db, user_id, for_update=True)
        
        available, staking_hold, paid_hold = self._compute_spending_split(balance, estimated_cost)
        
        if available < estimated_cost:
            logger.warning(
                "Insufficient balance for usage hold",
                event_type="insufficient_balance",
                user_id=user_id,
                request_id=request.request_id,
                ledger_entry_id=str(request.ledger_entry_id),
                available=str(available),
                estimated_cost=str(estimated_cost),
                is_staker=balance.is_staker,
                allow_overage=balance.allow_overage,
            )
            return UsageHoldResponse(
                success=False,
                error="insufficient_balance",
                estimated_cost=estimated_cost,
                available_balance=available,
            )
        
        # Hold amounts are negative (debit)
        hold_amount = -abs(estimated_cost)
        
        # Create ledger entry and update balance in a single transaction
        # Both operations use auto_commit=False, then we commit once at the end
        entry = await credits_crud.create_ledger_entry(
            db=db,
            user_id=user_id,
            entry_type=LedgerEntryType.usage_hold,
            status=LedgerStatus.pending,
            entry_id=request.ledger_entry_id,
            amount_paid=-paid_hold,  # Negative for hold
            amount_staking=-staking_hold,  # Negative for hold
            request_id=request.request_id,
            api_key_id=request.api_key_id,
            model_name=request.model_name,
            model_id=request.model_id,
            input_price_per_million=estimate.input_price_per_million,
            output_price_per_million=estimate.output_price_per_million,
            tokens_input=request.estimated_input_tokens,
            tokens_output=request.estimated_output_tokens,
            tokens_total=request.estimated_input_tokens + request.estimated_output_tokens,
            endpoint=request.endpoint,
            auto_commit=False,
        )
        
        # Update balance cache - deduct hold from appropriate buckets
        await credits_crud.update_balance(
            db=db,
            user_id=user_id,
            paid_holds_delta=-paid_hold,  # Negative (reduces paid_available)
            staking_delta=-staking_hold,  # Negative (reduces staking_available)
            auto_commit=False,
        )
        
        # Commit both ledger entry and balance update atomically
        await db.commit()
        await db.refresh(entry)
        
        logger.info(
            "Created usage hold",
            event_type="billing_hold_created",
            user_id=user_id,
            request_id=request.request_id,
            hold_amount=str(hold_amount),
            paid_hold=str(paid_hold),
            staking_hold=str(staking_hold),
            estimated_cost=str(estimated_cost),
            ledger_entry_id=str(entry.id),
        )
        
        return UsageHoldResponse(
            ledger_entry_id=entry.id,
            hold_amount=hold_amount,
            estimated_cost=estimated_cost,
            available_balance=available,
            success=True,
        )
    
    async def finalize_usage(
        self,
        db: AsyncSession,
        user_id: int,
        request: UsageFinalizeRequest,
    ) -> UsageFinalizeResponse:
        """
        Finalize usage by converting a hold to a posted charge.
        Updates the SAME ledger row and adjusts the balance cache.
        Implements staking-first spending strategy.
        """
        # Find the hold entry by ID (FOR UPDATE to prevent concurrent finalize/void)
        hold_entry = await credits_crud.get_ledger_entry_by_id(db, request.ledger_entry_id, for_update=True)
        
        if not hold_entry:
            logger.error(
                "Usage hold not found for finalization",
                user_id=user_id,
                ledger_entry_id=str(request.ledger_entry_id),
            )
            return UsageFinalizeResponse(
                ledger_entry_id=request.ledger_entry_id,
                amount_paid=Decimal("0"),
                amount_staking=Decimal("0"),
                amount_total=Decimal("0"),
                success=False,
                error="Hold not found",
            )
        
        # Check if already finalized
        if hold_entry.status == LedgerStatus.posted and hold_entry.entry_type == LedgerEntryType.usage_charge:
            logger.info(
                "Usage already finalized",
                user_id=user_id,
                ledger_entry_id=str(request.ledger_entry_id),
            )
            return UsageFinalizeResponse(
                ledger_entry_id=hold_entry.id,
                amount_paid=hold_entry.amount_paid,
                amount_staking=hold_entry.amount_staking,
                amount_total=hold_entry.amount_total,
                success=True,
            )
        
        # Get pricing from pricing service
        model_name = request.model_name or hold_entry.model_name
        model_id = request.model_id or hold_entry.model_id
        usage_cost = await self.pricing.calculate_cost(
            input_tokens=request.tokens_input,
            output_tokens=request.tokens_output,
            model_name=model_name,
            model_id=model_id,
            db=db,
        )
        
        total_cost = usage_cost.total_cost
        input_price_per_million = usage_cost.input_price_per_million
        output_price_per_million = usage_cost.output_price_per_million
        
        # Store the original hold amounts for cache adjustment (both are negative)
        original_hold_paid = hold_entry.amount_paid  # Negative
        original_hold_staking = hold_entry.amount_staking  # Negative
        
        # Get current balance for spending split logic.
        # The hold already drained staking_available, so we pass the held
        # staking amount back so the split sees the full pot reserved for
        # THIS transaction and doesn't shift everything to paid.
        balance = await credits_crud.get_or_create_balance(db, user_id)
        staking_reserved = abs(original_hold_staking)
        _available, staking_charge, paid_charge = self._compute_spending_split(
            balance, total_cost, staking_reserved=staking_reserved
        )
        
        # Update the ledger entry and balance in a single transaction
        hold_entry = await credits_crud.update_ledger_entry(
            db=db,
            entry=hold_entry,
            status=LedgerStatus.posted,
            entry_type=LedgerEntryType.usage_charge,
            amount_paid=-paid_charge,  # Negative for charge
            amount_staking=-staking_charge,  # Negative for charge
            tokens_input=request.tokens_input,
            tokens_output=request.tokens_output,
            tokens_total=request.tokens_total,
            input_price_per_million=input_price_per_million,
            output_price_per_million=output_price_per_million,
            model_name=model_name,
            model_id=request.model_id,
            endpoint=request.endpoint or hold_entry.endpoint,
            auto_commit=False,
        )
        
        # Update balance cache:
        # 1. Remove the old paid hold from pending_holds
        # 2. Apply the paid charge to posted_balance
        # 3. Release the old staking hold and apply staking charge
        #    Net staking delta = release_hold (-original_hold_staking) + charge (-staking_charge)
        await credits_crud.update_balance(
            db=db,
            user_id=user_id,
            paid_holds_delta=-original_hold_paid,  # Remove paid hold (add back the negative)
            paid_posted_delta=-paid_charge,  # Apply paid charge (negative)
            staking_delta=-original_hold_staking - staking_charge,  # Release staking hold + apply staking charge
            auto_commit=False,
        )
        
        # Commit both ledger update and balance update atomically
        await db.commit()
        await db.refresh(hold_entry)
        
        logger.info(
            "Finalized usage",
            user_id=user_id,
            ledger_entry_id=str(hold_entry.id),
            total_cost=str(total_cost),
            paid_charge=str(paid_charge),
            staking_charge=str(staking_charge),
        )
        
        return UsageFinalizeResponse(
            ledger_entry_id=hold_entry.id,
            amount_paid=-paid_charge,
            amount_staking=-staking_charge,
            amount_total=-total_cost,
            success=True,
        )
    
    async def void_usage(
        self,
        db: AsyncSession,
        user_id: int,
        request: UsageVoidRequest,
    ) -> UsageVoidResponse:
        """
        Void a usage hold when a request fails before producing billable output.
        """
        # Find the hold entry by ID (FOR UPDATE to prevent concurrent void/finalize)
        hold_entry = await credits_crud.get_ledger_entry_by_id(db, request.ledger_entry_id, for_update=True)
        
        if not hold_entry:
            logger.warning(
                "Usage hold not found for void",
                user_id=user_id,
                ledger_entry_id=str(request.ledger_entry_id),
            )
            return UsageVoidResponse(
                ledger_entry_id=request.ledger_entry_id,
                voided=False,
                error="Hold not found",
            )
        
        # Check if already voided or finalized
        if hold_entry.status == LedgerStatus.voided:
            return UsageVoidResponse(
                ledger_entry_id=hold_entry.id,
                voided=True,
            )
        
        if hold_entry.status == LedgerStatus.posted:
            return UsageVoidResponse(
                ledger_entry_id=hold_entry.id,
                voided=False,
                error="Cannot void a posted entry",
            )
        
        # Store original hold amounts for cache adjustment (both are negative)
        original_hold_paid = hold_entry.amount_paid  # Negative
        original_hold_staking = hold_entry.amount_staking  # Negative
        
        # Update the ledger entry and balance in a single transaction
        hold_entry = await credits_crud.update_ledger_entry(
            db=db,
            entry=hold_entry,
            status=LedgerStatus.voided,
            amount_paid=Decimal("0"),  # Zero out
            amount_staking=Decimal("0"),
            failure_code=request.failure_code,
            failure_reason=request.failure_reason,
            auto_commit=False,
        )
        
        # Update balance cache - release the hold from both buckets
        await credits_crud.update_balance(
            db=db,
            user_id=user_id,
            paid_holds_delta=-original_hold_paid,  # Release paid hold (add back the negative)
            staking_delta=-original_hold_staking,  # Release staking hold (add back the negative)
            auto_commit=False,
        )
        
        # Commit both ledger update and balance update atomically
        await db.commit()
        await db.refresh(hold_entry)
        
        logger.info(
            "Voided usage hold",
            user_id=user_id,
            ledger_entry_id=str(hold_entry.id),
        )
        
        return UsageVoidResponse(
            ledger_entry_id=hold_entry.id,
            voided=True,
        )
    
    # === Refund Operations ===
    
    async def create_refund(
        self,
        db: AsyncSession,
        user_id: int,
        request: RefundRequest,
    ) -> RefundResponse:
        """
        Create a refund for a previous usage charge.
        """
        idempotency_key = f"refund:{request.request_id}:{request.reason}"
        
        # Check for existing refund (idempotency)
        existing = await credits_crud.get_ledger_entry_by_idempotency_key(db, idempotency_key)
        if existing:
            logger.info(
                "Refund already exists",
                user_id=user_id,
                request_id=request.request_id,
            )
            return RefundResponse(
                ledger_entry_id=existing.id,
                amount_refunded=existing.amount_paid + existing.amount_staking,
                success=True,
            )
        
        # Find the original charge
        original_entry = await credits_crud.get_ledger_entry_by_request_id(db, user_id, request.request_id)
        
        if not original_entry:
            return RefundResponse(
                ledger_entry_id=uuid.uuid4(),
                amount_refunded=Decimal("0"),
                success=False,
                error="Original charge not found",
            )
        
        # Refund amount is positive (credit back to account)
        refund_amount = abs(request.amount)
        
        # Proportionally split between paid and staking based on original charge
        original_total = abs(original_entry.amount_paid) + abs(original_entry.amount_staking)
        if original_total > 0:
            paid_ratio = abs(original_entry.amount_paid) / original_total
            staking_ratio = abs(original_entry.amount_staking) / original_total
        else:
            paid_ratio = Decimal("1")
            staking_ratio = Decimal("0")
        
        refund_paid = refund_amount * paid_ratio
        refund_staking = refund_amount * staking_ratio
        
        # Create refund ledger entry and update balance in a single transaction
        entry = await credits_crud.create_ledger_entry(
            db=db,
            user_id=user_id,
            entry_type=LedgerEntryType.refund,
            status=LedgerStatus.posted,
            idempotency_key=idempotency_key,
            amount_paid=refund_paid,  # Positive for credit
            amount_staking=refund_staking,  # Positive for credit
            related_entry_id=original_entry.id,
            request_id=request.request_id,
            description=f"Refund: {request.reason}",
            auto_commit=False,
        )
        
        # Update balance cache
        await credits_crud.update_balance(
            db=db,
            user_id=user_id,
            paid_posted_delta=refund_paid,
            staking_delta=refund_staking,
            auto_commit=False,
        )
        
        # Commit both atomically
        await db.commit()
        await db.refresh(entry)
        
        logger.info(
            "Created refund",
            user_id=user_id,
            request_id=request.request_id,
            refund_amount=str(refund_amount),
            ledger_entry_id=str(entry.id),
        )
        
        return RefundResponse(
            ledger_entry_id=entry.id,
            amount_refunded=refund_amount,
            success=True,
        )
    
    # === Manual Credit Adjustment ===
    
    async def adjust_credits(
        self,
        db: AsyncSession,
        user_id: int,
        amount: Decimal,
        description: Optional[str] = None,
    ) -> Tuple[CreditLedger, Decimal]:
        """
        Adjust credits for an account (add or subtract).
        
        Args:
            amount: Positive to add credits, negative to subtract credits
            
        Returns (ledger_entry, new_paid_balance).
        """
        # Determine entry type based on amount sign
        if amount >= 0:
            entry_type = LedgerEntryType.purchase
            default_description = "Manual credit top-up"
        else:
            entry_type = LedgerEntryType.adjustment
            default_description = "Manual credit adjustment (deduction)"
        
        idempotency_key = f"adjust:{user_id}:{datetime.utcnow().isoformat()}:{uuid.uuid4()}"
        
        # Create ledger entry and update balance in a single transaction
        entry = await credits_crud.create_ledger_entry(
            db=db,
            user_id=user_id,
            entry_type=entry_type,
            status=LedgerStatus.posted,
            idempotency_key=idempotency_key,
            amount_paid=amount,  # Positive for credit, negative for debit
            amount_staking=Decimal("0"),  # Adjustments only affect paid bucket
            description=description or default_description,
            auto_commit=False,
        )
        
        # Update balance cache
        balance = await credits_crud.update_balance(
            db=db,
            user_id=user_id,
            paid_posted_delta=amount,
            auto_commit=False,
        )
        
        # Commit both atomically
        await db.commit()
        await db.refresh(entry)
        await db.refresh(balance)
        
        action = "Added" if amount >= 0 else "Subtracted"
        logger.info(
            f"{action} credits",
            user_id=user_id,
            amount=str(amount),
            new_balance=str(balance.paid_posted_balance),
            ledger_entry_id=str(entry.id),
        )
        
        return entry, balance.paid_posted_balance
    
    # Alias for backward compatibility
    async def add_credits(
        self,
        db: AsyncSession,
        user_id: int,
        amount: Decimal,
        description: Optional[str] = None,
    ) -> Tuple[CreditLedger, Decimal]:
        """Alias for adjust_credits (backward compatibility)."""
        return await self.adjust_credits(db, user_id, abs(amount), description)
    
    # === Automated Hold Reconciliation ===
    
    async def reconcile_stale_holds(
        self,
        db: AsyncSession,
        max_age_seconds: int,
    ) -> dict:
        """
        Void all usage_hold entries that have been pending longer than
        *max_age_seconds* and reconcile balances for affected users.

        Returns a summary dict with voided_count and affected_users.
        """
        voided_count, affected_user_ids = await credits_crud.void_stale_holds(
            db, max_age_seconds
        )

        if voided_count == 0:
            return {"voided_count": 0, "affected_users": 0}

        for user_id in affected_user_ids:
            try:
                await credits_crud.reconcile_all_balances(db, user_id)
            except Exception as e:
                logger.error(
                    "Failed to reconcile balance after stale hold cleanup",
                    user_id=user_id,
                    error=str(e),
                )

        logger.info(
            "Stale hold reconciliation complete",
            voided_count=voided_count,
            affected_users=len(affected_user_ids),
        )

        return {
            "voided_count": voided_count,
            "affected_users": len(affected_user_ids),
        }


# Singleton instance
billing_service = BillingService()

