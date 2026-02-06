"""
Billing API endpoints for credits management.
Provides REST API for viewing balance, transactions, spending metrics, and staking settings.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Header
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal
import secrets

from ....db.database import get_db_session
from ....db.models import User, LedgerEntryType
from ....dependencies import get_current_user, get_api_key_user
from ....services.billing_service import billing_service
from ....services.staking_service import staking_service
from ....crud import credits as credits_crud
from ....crud import user as user_crud
from ....schemas.billing import (
    BalanceResponse,
    LedgerEntryResponse,
    LedgerListResponse,
    MonthlySpendingResponse,
    MonthlySpending,
    SpendingModeEnum,
    UsageListResponse,
    UsageEntryResponse,
    StakingSettingsRequest,
    StakingSettingsResponse,
    StakingRefreshResponse,
    ManualTopupRequest,
    ManualTopupResponse,
    OverageSettingsRequest,
    OverageSettingsResponse,
    LedgerStatusEnum,
    LedgerEntryTypeEnum,
)
from ....core.logging_config import get_api_logger
from ....core.config import settings

logger = get_api_logger()

router = APIRouter(tags=["Billing"])


# === Admin Authentication ===

async def verify_billing_admin_secret(
    x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret")
) -> bool:
    """
    Verify the admin secret for protected billing endpoints.
    
    Requires the X-Admin-Secret header to match BILLING_ADMIN_SECRET env variable.
    """
    if not settings.BILLING_ADMIN_SECRET:
        logger.warning(
            "Billing admin endpoint called but BILLING_ADMIN_SECRET is not configured",
            event_type="billing_admin_not_configured"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin billing endpoints are not configured. Set BILLING_ADMIN_SECRET environment variable."
        )
    
    if not x_admin_secret:
        logger.warning(
            "Billing admin endpoint called without X-Admin-Secret header",
            event_type="billing_admin_missing_secret"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Admin-Secret header"
        )
    
    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(x_admin_secret, settings.BILLING_ADMIN_SECRET):
        logger.warning(
            "Billing admin endpoint called with invalid secret",
            event_type="billing_admin_invalid_secret"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin secret"
        )
    
    return True


# === Balance Endpoint ===

@router.get("/balance", response_model=BalanceResponse)
async def get_balance(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get current credit balance for the authenticated user.
    
    Returns:
    - paid: Paid bucket balance (posted, holds, available)
    - staking: Staking bucket balance (daily amount, refresh date, available)
    - total_available: Sum of all available credits
    """
    try:
        balance = await billing_service.get_balance(db, current_user.id)
        return balance
    except Exception as e:
        logger.error(
            "Error in get_balance endpoint",
            user_id=current_user.id,
            error=str(e),
            error_type=type(e).__name__,
            event_type="billing_balance_error"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching balance: {str(e)}"
        )


# === Overage Settings Endpoint ===

@router.put("/settings/overage", response_model=OverageSettingsResponse)
async def update_overage_setting(
    request: OverageSettingsRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Toggle the "Allow Overages" setting for the authenticated user.
    
    When **enabled** (`allow_overage: true`):
    - If your Daily Staking Allowance is exhausted, the system automatically
      deducts from your paid Credit Balance to prevent service interruption.
    
    When **disabled** (`allow_overage: false`, default):
    - Requests will fail with an `insufficient_balance` error once your
      Daily Staking Allowance is depleted. Your paid Credit Balance is not touched.
    """
    try:
        balance = await credits_crud.set_allow_overage(
            db=db,
            user_id=current_user.id,
            allow=request.allow_overage,
        )
        
        state = "enabled" if balance.allow_overage else "disabled"
        logger.info(
            f"Overage setting {state}",
            user_id=current_user.id,
            allow_overage=balance.allow_overage,
            event_type="billing_overage_setting_updated",
        )
        
        return OverageSettingsResponse(
            allow_overage=balance.allow_overage,
            message=f"Allow overages {state}",
        )
    except Exception as e:
        logger.error(
            "Error updating overage setting",
            user_id=current_user.id,
            error=str(e),
            error_type=type(e).__name__,
            event_type="billing_overage_setting_error",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating overage setting: {str(e)}",
        )


# === Transactions List Endpoint ===
@router.get("/transactions", response_model=LedgerListResponse)
async def list_transactions(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    entry_type: Optional[LedgerEntryTypeEnum] = Query(default=None),
    from_date: Optional[datetime] = Query(default=None, alias="from"),
    to_date: Optional[datetime] = Query(default=None, alias="to"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get paginated list of credit transactions (ledger entries).
    
    Parameters:
    - limit: Maximum number of items to return (1-100)
    - offset: Number of items to skip
    - type: Filter by entry type (purchase, usage_charge, refund, etc.)
    - from: Filter entries created after this datetime
    - to: Filter entries created before this datetime
    
    Returns newest entries first.
    """
    try:
        # Convert enum to model enum if provided
        model_entry_type = None
        if entry_type:
            model_entry_type = LedgerEntryType(entry_type.value)
        
        entries, total = await credits_crud.get_transactions(
            db=db,
            user_id=current_user.id,
            limit=limit,
            offset=offset,
            entry_type=model_entry_type,
            from_date=from_date,
            to_date=to_date,
        )
        
        items = [
            LedgerEntryResponse(
                id=entry.id,
                user_id=entry.user_id,
                currency=entry.currency,
                status=LedgerStatusEnum(entry.status.value),
                entry_type=LedgerEntryTypeEnum(entry.entry_type.value),
                amount_paid=entry.amount_paid,
                amount_staking=entry.amount_staking,
                amount_total=entry.amount_total,
                payment_source=entry.payment_source,
                external_transaction_id=entry.external_transaction_id,
                payment_metadata=entry.payment_metadata,
                idempotency_key=entry.idempotency_key,
                related_entry_id=entry.related_entry_id,
                request_id=entry.request_id,
                api_key_id=entry.api_key_id,
                model_name=entry.model_name,
                model_id=entry.model_id,
                endpoint=entry.endpoint,
                tokens_input=entry.tokens_input,
                tokens_output=entry.tokens_output,
                tokens_total=entry.tokens_total,
                input_price_per_million=entry.input_price_per_million,
                output_price_per_million=entry.output_price_per_million,
                failure_code=entry.failure_code,
                failure_reason=entry.failure_reason,
                description=entry.description,
                created_at=entry.created_at,
                updated_at=entry.updated_at,
            )
            for entry in entries
        ]
        
        return LedgerListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + len(items)) < total,
        )
    except Exception as e:
        logger.error(
            "Error in list_transactions endpoint",
            user_id=current_user.id,
            error=str(e),
            error_type=type(e).__name__,
            from_date=str(from_date) if from_date else None,
            to_date=str(to_date) if to_date else None,
            event_type="billing_transactions_error"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching transactions: {str(e)}"
        )


# === Spending Metrics Endpoint ===

@router.get("/spending", response_model=MonthlySpendingResponse)
async def get_monthly_spending(
    year: int = Query(default=None, description="Year for spending data (defaults to current year)"),
    mode: SpendingModeEnum = Query(default=SpendingModeEnum.gross),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get monthly spending metrics for a year.
    
    Parameters:
    - year: Year to get spending for (defaults to current year)
    - mode: 
      - gross: Only count usage charges
      - net: Include refunds in calculation
    
    Returns 12 months of data including months with zero spending.
    """
    try:
        if year is None:
            year = datetime.now().year
        
        include_refunds = mode == SpendingModeEnum.net
        
        monthly_data = await credits_crud.get_monthly_spending(
            db=db,
            user_id=current_user.id,
            year=year,
            include_refunds=include_refunds,
        )
        
        # Create a dict for easy lookup
        data_by_month = {m: (amount, count) for m, amount, count in monthly_data}
        
        # Build all 12 months
        months = []
        total = Decimal("0")
        for month in range(1, 13):
            amount, count = data_by_month.get(month, (Decimal("0"), 0))
            months.append(MonthlySpending(
                year=year,
                month=month,
                amount=amount,
                transaction_count=count,
            ))
            total += amount
        
        return MonthlySpendingResponse(
            year=year,
            mode=mode,
            months=months,
            total=total,
            currency="USD",
        )
    except Exception as e:
        logger.error(
            "Error in get_monthly_spending endpoint",
            user_id=current_user.id,
            error=str(e),
            error_type=type(e).__name__,
            year=year,
            event_type="billing_spending_error"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching spending data: {str(e)}"
        )


# === Usage List Endpoint ===

@router.get("/usage", response_model=UsageListResponse)
async def list_usage(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    from_date: Optional[datetime] = Query(default=None, alias="from"),
    to_date: Optional[datetime] = Query(default=None, alias="to"),
    model: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get paginated list of usage entries (posted usage charges only).
    
    Parameters:
    - limit: Maximum number of items to return (1-100)
    - offset: Number of items to skip
    - from: Filter entries created after this datetime
    - to: Filter entries created before this datetime
    - model: Filter by model name
    
    Returns newest entries first.
    """
    try:
        entries, total = await credits_crud.get_usage_entries(
            db=db,
            user_id=current_user.id,
            limit=limit,
            offset=offset,
            from_date=from_date,
            to_date=to_date,
            model_filter=model,
        )
        
        items = [
            UsageEntryResponse(
                id=entry.id,
                created_at=entry.created_at,
                model_name=entry.model_name,
                model_id=entry.model_id,
                endpoint=entry.endpoint,
                tokens_input=entry.tokens_input,
                tokens_output=entry.tokens_output,
                tokens_total=entry.tokens_total,
                amount_paid=entry.amount_paid,
                amount_staking=entry.amount_staking,
                amount_total=entry.amount_total,
                request_id=entry.request_id,
                api_key_id=entry.api_key_id,
            )
            for entry in entries
        ]
        
        return UsageListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + len(items)) < total,
        )
    except Exception as e:
        logger.error(
            "Error in list_usage endpoint",
            user_id=current_user.id,
            error=str(e),
            error_type=type(e).__name__,
            from_date=str(from_date) if from_date else None,
            to_date=str(to_date) if to_date else None,
            event_type="billing_usage_error"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching usage data: {str(e)}"
        )


@router.get("/usage/month", response_model=UsageListResponse)
async def list_usage_for_month(
    year: int = Query(..., description="Year"),
    month: int = Query(..., ge=1, le=12, description="Month (1-12)"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get paginated list of usage entries for a specific month.
    
    Parameters:
    - year: Year
    - month: Month (1-12)
    - limit: Maximum number of items to return (1-100)
    - offset: Number of items to skip
    
    Returns newest entries first.
    """
    try:
        # Calculate date range for the month
        from_date = datetime(year, month, 1, 0, 0, 0)
        if month == 12:
            to_date = datetime(year + 1, 1, 1, 0, 0, 0)
        else:
            to_date = datetime(year, month + 1, 1, 0, 0, 0)
        
        entries, total = await credits_crud.get_usage_entries(
            db=db,
            user_id=current_user.id,
            limit=limit,
            offset=offset,
            from_date=from_date,
            to_date=to_date,
        )
        
        items = [
            UsageEntryResponse(
                id=entry.id,
                created_at=entry.created_at,
                model_name=entry.model_name,
                model_id=entry.model_id,
                endpoint=entry.endpoint,
                tokens_input=entry.tokens_input,
                tokens_output=entry.tokens_output,
                tokens_total=entry.tokens_total,
                amount_paid=entry.amount_paid,
                amount_staking=entry.amount_staking,
                amount_total=entry.amount_total,
                request_id=entry.request_id,
                api_key_id=entry.api_key_id,
            )
            for entry in entries
        ]
        
        return UsageListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + len(items)) < total,
        )
    except Exception as e:
        logger.error(
            "Error in list_usage_for_month endpoint",
            user_id=current_user.id,
            error=str(e),
            error_type=type(e).__name__,
            year=year,
            month=month,
            event_type="billing_usage_month_error"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching usage data for month: {str(e)}"
        )


# === Staking Settings Endpoints (Admin Protected) ===

@router.post("/staking/settings", response_model=StakingSettingsResponse)
async def set_staking_settings(
    staking_request: StakingSettingsRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    _admin_verified: bool = Depends(verify_billing_admin_secret),
):
    """
    Set the daily staking allowance amount.
    
    **Admin/Dev endpoint** - Requires X-Admin-Secret header.
    
    This updates the configured daily amount but does NOT trigger an immediate refresh.
    The new amount will take effect on the next daily refresh.
    """
    try:
        balance = await credits_crud.set_staking_daily_amount(
            db=db,
            user_id=current_user.id,
            amount=staking_request.daily_amount,
        )
        
        logger.info(
            "Staking settings updated by admin",
            user_id=current_user.id,
            daily_amount=str(staking_request.daily_amount),
            event_type="billing_admin_staking_settings"
        )
        
        return StakingSettingsResponse(
            daily_amount=balance.staking_daily_amount,
            message="Staking daily amount updated",
        )
    except Exception as e:
        logger.error(
            "Error in set_staking_settings endpoint",
            user_id=current_user.id,
            error=str(e),
            error_type=type(e).__name__,
            event_type="billing_staking_settings_error"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating staking settings: {str(e)}"
        )


@router.post("/staking/refresh", response_model=StakingRefreshResponse)
async def trigger_staking_refresh(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    _admin_verified: bool = Depends(verify_billing_admin_secret),
):
    """
    Trigger the daily staking sync from Builders API.
    
    **Admin/Dev endpoint** - Requires X-Admin-Secret header.
    
    This operation:
    1. Fetches all stakers from Builders API
    2. Updates staked_amount for all linked wallets
    3. Calculates daily credits for each user (total_staked / 100)
    4. Creates ledger entries (transactions) for each user refresh
    5. Updates user balances
    
    Idempotent: Users already refreshed today will be skipped.
    The staking bucket resets to the calculated daily amount (does not accumulate).
    """
    logger.info(
        "Staking sync triggered by admin",
        user_id=current_user.id,
        event_type="billing_admin_staking_sync"
    )
    
    try:
        summary = await staking_service.run_daily_sync(db)
        
        return StakingRefreshResponse(
            success=summary.get("success", True),
            message="Staking sync completed successfully",
            stakers_fetched=summary.get("stakers_fetched"),
            total_wallets=summary.get("total_wallets"),
            wallets_updated=summary.get("wallets_updated"),
            users_processed=summary.get("users_processed"),
            users_skipped=summary.get("users_skipped"),
            users_failed=summary.get("users_failed"),
            duration_seconds=summary.get("duration_seconds"),
        )
    except Exception as e:
        logger.error(
            "Staking sync failed",
            user_id=current_user.id,
            error=str(e),
            event_type="billing_admin_staking_sync_failed"
        )
        return StakingRefreshResponse(
            success=False,
            message=f"Staking sync failed: {str(e)}",
        )


# === Manual Credit Top-up (Admin/Dev endpoint) ===

@router.post("/credits/adjust", response_model=ManualTopupResponse)
async def adjust_credits(
    request: ManualTopupRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    _admin_verified: bool = Depends(verify_billing_admin_secret),
):
    """
    Manually adjust credits for an account (add or subtract).
    
    **Admin/Dev endpoint** - Requires X-Admin-Secret header.
    
    - Positive amount: Adds credits (simulates a purchase)
    - Negative amount: Subtracts credits (admin correction/chargeback)
    - user_id (optional): Target user ID (database primary key integer)
    - cognito_user_id (optional): Target Cognito user ID (UUID)
    - If neither provided, adjusts current user's credits.
    
    This endpoint is for development/admin purposes to manage credits
    without integrating with payment providers.
    """
    try:
        # Determine target user ID
        target_user_id = current_user.id
        
        if request.user_id is not None:
            target_user_id = request.user_id
        elif request.cognito_user_id is not None:
            # Look up user by cognito_user_id
            target_user = await user_crud.get_user_by_cognito_id(db, str(request.cognito_user_id))
            if not target_user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User with cognito_user_id {request.cognito_user_id} not found"
                )
            target_user_id = target_user.id
        
        entry, new_balance = await billing_service.adjust_credits(
            db=db,
            user_id=target_user_id,
            amount=request.amount_usd,
            description=request.description,
        )
        
        action = "added" if request.amount_usd >= 0 else "subtracted"
        logger.info(
            f"Manual credit adjustment by admin: {action}",
            user_id=str(target_user_id),
            admin_user_id=str(current_user.id),
            amount=str(request.amount_usd),
            new_balance=str(new_balance),
            event_type="billing_admin_credit_adjust"
        )
        
        message = f"Credits {action} successfully"
        
        return ManualTopupResponse(
            ledger_entry_id=entry.id,
            amount_added=request.amount_usd,
            new_paid_balance=new_balance,
            message=message,
        )
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(
            "Error in adjust_credits endpoint",
            user_id=current_user.id,
            error=str(e),
            error_type=type(e).__name__,
            event_type="billing_credit_adjust_error"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error adjusting credits: {str(e)}"
        )


# Export router
billing_router = router

