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
    
    async def get_balance(self, db: AsyncSession, user_id: int) -> BalanceResponse:
        """
        Get the current balance for an account.
        """
        balance = await credits_crud.get_or_create_balance(db, user_id)
        
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
        
        # Check balance
        balance = await credits_crud.get_or_create_balance(db, user_id)
        available = balance.total_available
        
        if available < estimated_cost:
            logger.warning(
                "Insufficient balance for usage hold",
                user_id=user_id,
                request_id=request.request_id,
                ledger_entry_id=str(request.ledger_entry_id),
                available=str(available),
                estimated_cost=str(estimated_cost),
            )
            return UsageHoldResponse(
                success=False,
                error="insufficient_balance",
                estimated_cost=estimated_cost,
                available_balance=available,
            )
        
        # Hold amount is negative (debit)
        hold_amount = -abs(estimated_cost)
        
        # Create ledger entry with the provided ID
        entry = await credits_crud.create_ledger_entry(
            db=db,
            user_id=user_id,
            entry_type=LedgerEntryType.usage_hold,
            status=LedgerStatus.pending,
            entry_id=request.ledger_entry_id,
            amount_paid=hold_amount,  # Negative for hold
            amount_staking=Decimal("0"),
            request_id=request.request_id,
            api_key_id=request.api_key_id,
            model_name=request.model_name,
            model_id=request.model_id,
            endpoint=request.endpoint,
        )
        
        # Update balance cache - add to pending holds
        await credits_crud.update_balance(
            db=db,
            user_id=user_id,
            paid_holds_delta=hold_amount,  # Negative
        )
        
        logger.info(
            "Created usage hold",
            user_id=user_id,
            request_id=request.request_id,
            hold_amount=str(hold_amount),
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
        # Find the hold entry by ID
        hold_entry = await credits_crud.get_ledger_entry_by_id(db, request.ledger_entry_id)
        
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
        
        # Get current balance for staking-first logic
        balance = await credits_crud.get_or_create_balance(db, user_id)
        staking_available = balance.staking_available or Decimal("0")
        
        # Spend staking first
        staking_charge = min(total_cost, staking_available)
        paid_charge = total_cost - staking_charge
        
        # Store the original hold amount for cache adjustment
        original_hold = hold_entry.amount_paid  # Negative
        
        # Update the ledger entry (convert hold to charge)
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
        )
        
        # Update balance cache:
        # 1. Remove the old hold from pending_holds
        # 2. Apply the paid charge to posted_balance
        # 3. Decrease staking_available
        await credits_crud.update_balance(
            db=db,
            user_id=user_id,
            paid_holds_delta=-original_hold,  # Remove old hold (add back the negative)
            paid_posted_delta=-paid_charge,  # Apply charge (negative)
            staking_delta=-staking_charge,  # Decrease staking
        )
        
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
        # Find the hold entry by ID
        hold_entry = await credits_crud.get_ledger_entry_by_id(db, request.ledger_entry_id)
        
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
        
        # Store original hold amount for cache adjustment
        original_hold = hold_entry.amount_paid  # Negative
        
        # Update the ledger entry to voided
        hold_entry = await credits_crud.update_ledger_entry(
            db=db,
            entry=hold_entry,
            status=LedgerStatus.voided,
            amount_paid=Decimal("0"),  # Zero out
            amount_staking=Decimal("0"),
            failure_code=request.failure_code,
            failure_reason=request.failure_reason,
        )
        
        # Update balance cache - remove the hold
        await credits_crud.update_balance(
            db=db,
            user_id=user_id,
            paid_holds_delta=-original_hold,  # Remove the hold (add back the negative)
        )
        
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
        
        # Create refund ledger entry
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
        )
        
        # Update balance cache
        await credits_crud.update_balance(
            db=db,
            user_id=user_id,
            paid_posted_delta=refund_paid,
            staking_delta=refund_staking,
        )
        
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
        
        # Create ledger entry - amount sign determines credit vs debit
        entry = await credits_crud.create_ledger_entry(
            db=db,
            user_id=user_id,
            entry_type=entry_type,
            status=LedgerStatus.posted,
            idempotency_key=idempotency_key,
            amount_paid=amount,  # Positive for credit, negative for debit
            amount_staking=Decimal("0"),  # Adjustments only affect paid bucket
            description=description or default_description,
        )
        
        # Update balance cache
        balance = await credits_crud.update_balance(
            db=db,
            user_id=user_id,
            paid_posted_delta=amount,
        )
        
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


# Singleton instance
billing_service = BillingService()

