"""
CRUD operations for API Credits
"""

import uuid
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from ..db.billing_models import (
    APICredit,
    APICreditTransaction,
    CreditTransactionType
)
from ..core.logging_config import get_api_logger

logger = get_api_logger()


async def get_or_create_api_credit(
    db: AsyncSession,
    user_id: int
) -> APICredit:
    """
    Get or create API credit account for a user.

    Args:
        db: Database session
        user_id: User ID

    Returns:
        APICredit object
    """
    result = await db.execute(
        select(APICredit).where(APICredit.user_id == user_id)
    )
    api_credit = result.scalar_one_or_none()

    if not api_credit:
        api_credit = APICredit(
            user_id=user_id,
            balance=Decimal("0.00"),
            staking_balance=Decimal("0.00"),
            staking_daily_amount=Decimal("0.00"),
            total_earned=Decimal("0.00"),
            total_spent=Decimal("0.00"),
            total_refunded=Decimal("0.00")
        )
        db.add(api_credit)
        await db.commit()
        await db.refresh(api_credit)

        logger.info("Created API credit account",
                   user_id=user_id,
                   event_type="api_credit_created")

    return api_credit


async def get_balance(
    db: AsyncSession,
    user_id: int
) -> Decimal:
    """
    Get user's current API credit balance.

    Args:
        db: Database session
        user_id: User ID

    Returns:
        Balance as Decimal
    """
    api_credit = await get_or_create_api_credit(db, user_id)
    return api_credit.balance


async def add_credits(
    db: AsyncSession,
    user_id: int,
    amount: Decimal,
    transaction_type: CreditTransactionType,
    payment_method: Optional[str] = None,
    payment_id: Optional[str] = None,
    payment_metadata: Optional[dict] = None,
    description: Optional[str] = None
) -> APICreditTransaction:
    """
    Add credits to a user's account.

    Args:
        db: Database session
        user_id: User ID
        amount: Amount to add (positive)
        transaction_type: Type of transaction
        payment_method: Payment method used
        payment_id: External payment ID
        payment_metadata: Additional payment metadata
        description: Transaction description

    Returns:
        Created transaction
    """
    if amount <= 0:
        raise ValueError("Amount must be positive")

    api_credit = await get_or_create_api_credit(db, user_id)

    # Update balance
    new_balance = api_credit.balance + amount
    api_credit.balance = new_balance
    api_credit.total_earned += amount

    # Create transaction record
    transaction = APICreditTransaction(
        id=str(uuid.uuid4()),
        user_id=user_id,
        api_credit_id=api_credit.id,
        type=transaction_type,
        amount=amount,
        balance_after=new_balance,
        payment_method=payment_method,
        payment_id=payment_id,
        payment_metadata=payment_metadata,
        description=description
    )

    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)

    logger.info("Added credits to account",
               user_id=user_id,
               amount=str(amount),
               new_balance=str(new_balance),
               transaction_type=transaction_type.value,
               event_type="credits_added")

    return transaction


async def deduct_credits(
    db: AsyncSession,
    user_id: int,
    amount: Decimal,
    request_id: Optional[str] = None,
    model: Optional[str] = None,
    tokens_input: Optional[int] = None,
    tokens_output: Optional[int] = None,
    price_per_input_token: Optional[Decimal] = None,
    price_per_output_token: Optional[Decimal] = None,
    description: Optional[str] = None
) -> APICreditTransaction:
    """
    Deduct credits from a user's account for inference usage.

    Args:
        db: Database session
        user_id: User ID
        amount: Amount to deduct (positive value)
        request_id: Associated request ID
        model: Model used
        tokens_input: Input tokens
        tokens_output: Output tokens
        price_per_input_token: Price per input token
        price_per_output_token: Price per output token
        description: Transaction description

    Returns:
        Created transaction

    Raises:
        ValueError: If insufficient balance
    """
    if amount <= 0:
        raise ValueError("Amount must be positive")

    api_credit = await get_or_create_api_credit(db, user_id)

    # Check balance
    if api_credit.balance < amount:
        logger.error("Insufficient balance for deduction",
                    user_id=user_id,
                    balance=str(api_credit.balance),
                    amount=str(amount),
                    event_type="insufficient_balance")
        raise ValueError(f"Insufficient balance. Have: {api_credit.balance}, Need: {amount}")

    # Update balance
    new_balance = api_credit.balance - amount
    api_credit.balance = new_balance
    api_credit.total_spent += amount

    # Create transaction record (negative amount)
    transaction = APICreditTransaction(
        id=str(uuid.uuid4()),
        user_id=user_id,
        api_credit_id=api_credit.id,
        type=CreditTransactionType.deduction,
        amount=-amount,  # Negative for deduction
        balance_after=new_balance,
        request_id=request_id,
        model=model,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        tokens_total=(tokens_input or 0) + (tokens_output or 0),
        price_per_input_token=price_per_input_token,
        price_per_output_token=price_per_output_token,
        description=description
    )

    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)

    logger.info("Deducted credits from account",
               user_id=user_id,
               amount=str(amount),
               new_balance=str(new_balance),
               request_id=request_id,
               model=model,
               event_type="credits_deducted")

    return transaction


async def refund_credits(
    db: AsyncSession,
    user_id: int,
    amount: Decimal,
    request_id: Optional[str] = None,
    description: Optional[str] = None
) -> APICreditTransaction:
    """
    Refund credits to a user's account (e.g., for failed request).

    Args:
        db: Database session
        user_id: User ID
        amount: Amount to refund (positive)
        request_id: Associated request ID
        description: Refund reason

    Returns:
        Created transaction
    """
    if amount <= 0:
        raise ValueError("Amount must be positive")

    api_credit = await get_or_create_api_credit(db, user_id)

    # Update balance
    new_balance = api_credit.balance + amount
    api_credit.balance = new_balance
    api_credit.total_refunded += amount

    # Create transaction record
    transaction = APICreditTransaction(
        id=str(uuid.uuid4()),
        user_id=user_id,
        api_credit_id=api_credit.id,
        type=CreditTransactionType.refund,
        amount=amount,
        balance_after=new_balance,
        request_id=request_id,
        description=description
    )

    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)

    logger.info("Refunded credits to account",
               user_id=user_id,
               amount=str(amount),
               new_balance=str(new_balance),
               request_id=request_id,
               event_type="credits_refunded")

    return transaction


async def refresh_staking_credits(
    db: AsyncSession,
    user_id: int
) -> Optional[APICreditTransaction]:
    """
    Refresh daily staking credits for a user.

    This should be called once per day for users with staking rewards.

    Args:
        db: Database session
        user_id: User ID

    Returns:
        Transaction if credits were refreshed, None otherwise
    """
    api_credit = await get_or_create_api_credit(db, user_id)

    # Check if refresh is needed
    today = date.today()
    if api_credit.staking_refresh_date == today:
        logger.debug("Staking credits already refreshed today",
                    user_id=user_id,
                    event_type="staking_already_refreshed")
        return None

    if api_credit.staking_daily_amount <= 0:
        logger.debug("No staking credits configured for user",
                    user_id=user_id,
                    event_type="no_staking_credits")
        return None

    # Refresh credits
    amount = api_credit.staking_daily_amount
    new_balance = api_credit.balance + amount
    api_credit.balance = new_balance
    api_credit.staking_balance += amount
    api_credit.total_earned += amount
    api_credit.staking_refresh_date = today

    # Create transaction record
    transaction = APICreditTransaction(
        id=str(uuid.uuid4()),
        user_id=user_id,
        api_credit_id=api_credit.id,
        type=CreditTransactionType.earn_staking,
        amount=amount,
        balance_after=new_balance,
        payment_method="staking",
        description=f"Daily staking reward for {today}"
    )

    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)

    logger.info("Refreshed daily staking credits",
               user_id=user_id,
               amount=str(amount),
               new_balance=str(new_balance),
               event_type="staking_credits_refreshed")

    return transaction


async def get_transaction_history(
    db: AsyncSession,
    user_id: int,
    limit: int = 100,
    offset: int = 0,
    transaction_type: Optional[CreditTransactionType] = None
) -> List[APICreditTransaction]:
    """
    Get transaction history for a user.

    Args:
        db: Database session
        user_id: User ID
        limit: Maximum number of transactions to return
        offset: Offset for pagination
        transaction_type: Filter by transaction type

    Returns:
        List of transactions
    """
    query = select(APICreditTransaction).where(
        APICreditTransaction.user_id == user_id
    )

    if transaction_type:
        query = query.where(APICreditTransaction.type == transaction_type)

    query = query.order_by(APICreditTransaction.created_at.desc())
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    return result.scalars().all()


async def get_monthly_spending(
    db: AsyncSession,
    user_id: int,
    year: int,
    month: int
) -> Decimal:
    """
    Get total spending for a specific month.

    Args:
        db: Database session
        user_id: User ID
        year: Year
        month: Month (1-12)

    Returns:
        Total spending as Decimal
    """
    from datetime import datetime
    from calendar import monthrange

    start_date = datetime(year, month, 1)
    _, last_day = monthrange(year, month)
    end_date = datetime(year, month, last_day, 23, 59, 59)

    result = await db.execute(
        select(APICreditTransaction)
        .where(
            and_(
                APICreditTransaction.user_id == user_id,
                APICreditTransaction.type == CreditTransactionType.deduction,
                APICreditTransaction.created_at >= start_date,
                APICreditTransaction.created_at <= end_date
            )
        )
    )

    transactions = result.scalars().all()
    total = sum(abs(t.amount) for t in transactions)

    return Decimal(str(total))


async def set_staking_daily_amount(
    db: AsyncSession,
    user_id: int,
    daily_amount: Decimal
) -> APICredit:
    """
    Set the daily staking credit amount for a user.

    Args:
        db: Database session
        user_id: User ID
        daily_amount: Daily credit amount

    Returns:
        Updated APICredit
    """
    api_credit = await get_or_create_api_credit(db, user_id)
    api_credit.staking_daily_amount = daily_amount

    await db.commit()
    await db.refresh(api_credit)

    logger.info("Set daily staking amount",
               user_id=user_id,
               daily_amount=str(daily_amount),
               event_type="staking_daily_amount_set")

    return api_credit
