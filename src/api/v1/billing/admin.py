"""
Admin billing API endpoints.
Protected by X-Admin-Secret header. Served on /admin/docs Swagger page.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Header
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import secrets

from ....db.database import get_db_session
from ....db.models import User
from ....dependencies import get_current_user
from ....services.billing_service import billing_service
from ....services.staking_service import staking_service
from ....crud import credits as credits_crud
from ....crud import user as user_crud
from ....schemas.billing import (
    BalanceResponse,
    StakingSettingsRequest,
    StakingSettingsResponse,
    StakingRefreshResponse,
    ManualTopupRequest,
    ManualTopupResponse,
    RateLimitMultiplierRequest,
    RateLimitMultiplierResponse,
)
from ....core.logging_config import get_api_logger
from ....core.config import settings
from ....services.cache_service import cache_service

logger = get_api_logger()

admin_router = APIRouter(tags=["Billing Admin"])


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


# === Staking Settings Endpoints ===

@admin_router.post("/staking/settings", response_model=StakingSettingsResponse)
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


@admin_router.post("/staking/refresh", response_model=StakingRefreshResponse)
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


# === Manual Credit Adjustment ===

@admin_router.post("/credits/adjust", response_model=ManualTopupResponse)
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
        target_user_id = current_user.id
        
        if request.user_id is not None:
            target_user_id = request.user_id
        elif request.cognito_user_id is not None:
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
        
        return ManualTopupResponse(
            ledger_entry_id=entry.id,
            amount_added=request.amount_usd,
            new_paid_balance=new_balance,
            message=f"Credits {action} successfully",
        )
    except HTTPException:
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


# === Balance Reconciliation ===

@admin_router.post("/balance/reconcile", response_model=BalanceResponse)
async def reconcile_balance(
    user_id: Optional[int] = Query(default=None, description="Target user ID (defaults to current user)"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    _admin_verified: bool = Depends(verify_billing_admin_secret),
):
    """
    Reconcile the cached balance against the ledger (source of truth).

    **Admin/Dev endpoint** - Requires X-Admin-Secret header.

    Fixes drift in `paid_pending_holds` caused by partial transaction failures
    where the ledger entry was updated but the balance cache was not.

    Recomputes `paid_pending_holds` from the sum of all pending usage_hold entries in the ledger.

    Parameters:
    - user_id: Target user ID (defaults to current user)
    """
    try:
        target_user_id = user_id if user_id is not None else current_user.id
        
        logger.info(
            "Balance reconciliation triggered by admin",
            target_user_id=target_user_id,
            admin_user_id=current_user.id,
            event_type="billing_admin_reconcile",
        )
        
        result = await billing_service.reconcile_balance(db, target_user_id)
        return result
    except Exception as e:
        logger.error(
            "Error in reconcile_balance endpoint",
            user_id=current_user.id,
            error=str(e),
            error_type=type(e).__name__,
            event_type="billing_reconcile_error",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error reconciling balance: {str(e)}",
        )


# === Rate Limit Multiplier ===

@admin_router.post("/rate-limit/multiplier", response_model=RateLimitMultiplierResponse)
async def set_rate_limit_multiplier(
    request: RateLimitMultiplierRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    _admin_verified: bool = Depends(verify_billing_admin_secret),
):
    """
    Set the rate limit multiplier for a user.

    **Admin endpoint** - Requires X-Admin-Secret header.

    The multiplier scales all RPM/TPM limits for the target user:
    - 1.0 = default limits
    - 2.0 = double the limits
    - 0.5 = half the limits

    Applies to all models. Takes effect on the next request.
    """
    try:
        target_user = await user_crud.get_user_by_cognito_id(db, request.cognito_user_id)
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with cognito_user_id {request.cognito_user_id} not found",
            )

        target_user.rate_limit_multiplier = request.multiplier
        await db.commit()
        await db.refresh(target_user)

        await cache_service.delete("user", request.cognito_user_id)

        logger.info(
            "Rate limit multiplier updated by admin",
            target_user_id=target_user.id,
            target_cognito_id=request.cognito_user_id,
            admin_user_id=current_user.id,
            multiplier=request.multiplier,
            event_type="admin_rate_limit_multiplier_set",
        )

        return RateLimitMultiplierResponse(
            cognito_user_id=target_user.cognito_user_id,
            user_id=target_user.id,
            multiplier=target_user.rate_limit_multiplier,
            message=f"Rate limit multiplier set to {request.multiplier}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error setting rate limit multiplier",
            error=str(e),
            error_type=type(e).__name__,
            event_type="admin_rate_limit_multiplier_error",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error setting rate limit multiplier: {str(e)}",
        )


@admin_router.get("/rate-limit/multiplier/{cognito_user_id}", response_model=RateLimitMultiplierResponse)
async def get_rate_limit_multiplier(
    cognito_user_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    _admin_verified: bool = Depends(verify_billing_admin_secret),
):
    """
    Get the current rate limit multiplier for a user.

    **Admin endpoint** - Requires X-Admin-Secret header.
    """
    try:
        target_user = await user_crud.get_user_by_cognito_id(db, cognito_user_id)
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with cognito_user_id {cognito_user_id} not found",
            )

        return RateLimitMultiplierResponse(
            cognito_user_id=target_user.cognito_user_id,
            user_id=target_user.id,
            multiplier=target_user.rate_limit_multiplier,
            message="Current rate limit multiplier",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error getting rate limit multiplier",
            error=str(e),
            error_type=type(e).__name__,
            event_type="admin_rate_limit_multiplier_get_error",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting rate limit multiplier: {str(e)}",
        )
