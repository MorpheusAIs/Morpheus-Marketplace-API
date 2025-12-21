"""
API Credits Balance Endpoints

GET /api/v1/credits/balance - Get current balance
GET /api/v1/credits/transactions - Get transaction history
GET /api/v1/credits/spending - Get spending metrics
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from decimal import Decimal
from datetime import datetime

from ....db.database import get_db
from ....dependencies import get_current_user
from ....db.models import User
from ....crud import api_credits
from ....db.billing_models import CreditTransactionType
from ....schemas.credits import (
    BalanceResponse,
    TransactionResponse,
    TransactionListResponse,
    SpendingMetricsResponse
)
from ....core.logging_config import get_api_logger

logger = get_api_logger()
router = APIRouter()


@router.get("/balance", response_model=BalanceResponse)
async def get_balance(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get current API credit balance.

    Returns:
        - balance: Current balance
        - staking_balance: Balance from staking
        - staking_daily_amount: Daily refresh amount
        - total_earned: Lifetime earned
        - total_spent: Lifetime spent
        - total_refunded: Lifetime refunded
    """
    api_credit = await api_credits.get_or_create_api_credit(db, current_user.id)

    return BalanceResponse(
        balance=api_credit.balance,
        staking_balance=api_credit.staking_balance,
        staking_daily_amount=api_credit.staking_daily_amount,
        staking_refresh_date=api_credit.staking_refresh_date,
        total_earned=api_credit.total_earned,
        total_spent=api_credit.total_spent,
        total_refunded=api_credit.total_refunded,
        last_updated=api_credit.updated_at
    )


@router.get("/transactions", response_model=TransactionListResponse)
async def get_transactions(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    transaction_type: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get transaction history.

    Query parameters:
        - limit: Maximum transactions to return (1-500, default 100)
        - offset: Pagination offset
        - transaction_type: Filter by type (optional)

    Returns:
        List of transactions with pagination info
    """
    # Validate transaction type if provided
    tx_type = None
    if transaction_type:
        try:
            tx_type = CreditTransactionType[transaction_type]
        except KeyError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid transaction type: {transaction_type}"
            )

    transactions = await api_credits.get_transaction_history(
        db=db,
        user_id=current_user.id,
        limit=limit,
        offset=offset,
        transaction_type=tx_type
    )

    return TransactionListResponse(
        transactions=[
            TransactionResponse(
                id=tx.id,
                type=tx.type.value,
                amount=tx.amount,
                balance_after=tx.balance_after,
                payment_method=tx.payment_method,
                payment_id=tx.payment_id,
                request_id=tx.request_id,
                model=tx.model,
                tokens_input=tx.tokens_input,
                tokens_output=tx.tokens_output,
                tokens_total=tx.tokens_total,
                price_per_input_token=tx.price_per_input_token,
                price_per_output_token=tx.price_per_output_token,
                description=tx.description,
                created_at=tx.created_at
            )
            for tx in transactions
        ],
        limit=limit,
        offset=offset,
        total=len(transactions)
    )


@router.get("/spending", response_model=SpendingMetricsResponse)
async def get_spending_metrics(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get spending metrics.

    Query parameters:
        - year: Year (default: current year)
        - month: Month 1-12 (default: current month)

    Returns:
        Monthly spending breakdown
    """
    now = datetime.now()
    year = year or now.year
    month = month or now.month

    total_spending = await api_credits.get_monthly_spending(
        db=db,
        user_id=current_user.id,
        year=year,
        month=month
    )

    return SpendingMetricsResponse(
        year=year,
        month=month,
        total_spending=total_spending,
        currency="USD"
    )
