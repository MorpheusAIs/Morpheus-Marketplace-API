"""
CRUD operations for credits ledger and account balances.
"""
from typing import Optional, List, Tuple
from datetime import datetime, date, timezone
from decimal import Decimal
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, and_, extract
from sqlalchemy.orm import selectinload

from src.core.config import settings
from src.db.models import CreditLedger, CreditAccountBalance, LedgerStatus, LedgerEntryType
from src.core.logging_config import get_core_logger

logger = get_core_logger()


def _normalize_datetime(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None

    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    return dt


# === Account Balance Operations ===

async def get_or_create_balance(
    db: AsyncSession, user_id: int, for_update: bool = False
) -> CreditAccountBalance:
    """
    Get account balance record, creating one if it doesn't exist.
    
    Args:
        for_update: If True, acquires a row-level lock (SELECT ... FOR UPDATE).
                    The lock is held until the transaction commits, serialising
                    concurrent operations on the same user's balance.  Use this
                    when the caller needs to read-then-write (e.g. balance
                    sufficiency check followed by a hold creation).
    """
    query = select(CreditAccountBalance).where(CreditAccountBalance.user_id == user_id)
    if for_update:
        query = query.with_for_update()
    
    result = await db.execute(query)
    balance = result.scalar_one_or_none()
    
    if not balance:
        balance = CreditAccountBalance(
            user_id=user_id,
            paid_posted_balance=Decimal(settings.DEFAULT_BALANCE_AMOUNT),
            paid_pending_holds=Decimal("0"),
            staking_daily_amount=Decimal("0"),
            staking_available=Decimal("0"),
            staking_refresh_date=None,
        )
        db.add(balance)
        await db.commit()
        await db.refresh(balance)
        logger.info("Created new account balance record", user_id=user_id)
        
        # If caller requested a lock, re-acquire with FOR UPDATE
        if for_update:
            result = await db.execute(
                select(CreditAccountBalance)
                .where(CreditAccountBalance.user_id == user_id)
                .with_for_update()
            )
            balance = result.scalar_one()
    
    return balance


async def update_balance(
    db: AsyncSession,
    user_id: int,
    paid_posted_delta: Decimal = Decimal("0"),
    paid_holds_delta: Decimal = Decimal("0"),
    staking_delta: Decimal = Decimal("0"),
    staking_daily_amount: Optional[Decimal] = None,
    staking_refresh_date: Optional[date] = None,
    auto_commit: bool = True,
) -> CreditAccountBalance:
    """
    Update account balance using SQL-level atomic arithmetic.
    
    Uses ``UPDATE ... SET column = column + delta`` to prevent lost-update race
    conditions under concurrent access.  Python-level read-modify-write is NOT
    used for delta operations.
    
    Deltas are added to existing values via server-side expressions.
    Optional values (staking_daily_amount, staking_refresh_date) replace if provided.
    
    Args:
        auto_commit: If False, the UPDATE is executed but not committed.
                     Caller is responsible for committing the transaction.
    """
    # Ensure the record exists (no-op for existing users)
    await get_or_create_balance(db, user_id)
    
    # Build SQL UPDATE with server-side arithmetic for deltas.
    # This is atomic at the database level – concurrent transactions each see
    # the latest committed value when the UPDATE executes, eliminating
    # the lost-update race inherent in Python-level read-modify-write.
    values: dict = {
        "updated_at": datetime.utcnow(),
    }
    
    if paid_posted_delta != Decimal("0"):
        values["paid_posted_balance"] = (
            func.coalesce(CreditAccountBalance.paid_posted_balance, 0) + paid_posted_delta
        )
    if paid_holds_delta != Decimal("0"):
        values["paid_pending_holds"] = (
            func.coalesce(CreditAccountBalance.paid_pending_holds, 0) + paid_holds_delta
        )
    if staking_delta != Decimal("0"):
        values["staking_available"] = (
            func.coalesce(CreditAccountBalance.staking_available, 0) + staking_delta
        )
    
    # Absolute replacements (not deltas)
    if staking_daily_amount is not None:
        values["staking_daily_amount"] = staking_daily_amount
    if staking_refresh_date is not None:
        values["staking_refresh_date"] = staking_refresh_date
    
    stmt = (
        update(CreditAccountBalance)
        .where(CreditAccountBalance.user_id == user_id)
        .values(**values)
    )
    await db.execute(stmt)
    
    if auto_commit:
        await db.commit()
    
    # Expire any stale ORM-cached object and re-read the updated row
    # (the raw UPDATE bypasses ORM identity map, so cached objects are stale)
    result = await db.execute(
        select(CreditAccountBalance)
        .where(CreditAccountBalance.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    balance = result.scalar_one()
    
    return balance


async def set_staking_daily_amount(db: AsyncSession, user_id: int, amount: Decimal) -> CreditAccountBalance:
    """
    Set the staking daily amount for an account.
    """
    balance = await get_or_create_balance(db, user_id)
    balance.staking_daily_amount = amount
    balance.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(balance)
    
    logger.info("Updated staking daily amount", user_id=user_id, amount=str(amount))
    return balance


async def set_allow_overage(db: AsyncSession, user_id: int, allow: bool) -> CreditAccountBalance:
    """
    Toggle the allow_overage flag for an account.
    
    When enabled, the system automatically deducts from the paid Credit Balance
    after the Daily Staking Allowance is exhausted.
    """
    balance = await get_or_create_balance(db, user_id)
    balance.allow_overage = allow
    balance.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(balance)
    
    logger.info("Updated allow_overage setting", user_id=user_id, allow_overage=allow)
    return balance


# === Balance Reconciliation ===

async def reconcile_pending_holds(db: AsyncSession, user_id: int) -> CreditAccountBalance:
    """
    Reconcile paid_pending_holds by recomputing from the ledger (source of truth).
    
    Sums amount_paid for all pending usage_hold entries and sets paid_pending_holds
    to match. This fixes drift caused by partial commits where the ledger was updated
    but the balance cache was not.
    
    Returns the updated balance record.
    """
    # Sum amount_paid from all PENDING usage_hold entries (should be negative or zero)
    result = await db.execute(
        select(func.coalesce(func.sum(CreditLedger.amount_paid), Decimal("0"))).where(
            CreditLedger.user_id == user_id,
            CreditLedger.entry_type == LedgerEntryType.usage_hold,
            CreditLedger.status == LedgerStatus.pending,
        )
    )
    actual_pending_holds = result.scalar() or Decimal("0")
    
    balance = await get_or_create_balance(db, user_id)
    old_value = balance.paid_pending_holds or Decimal("0")
    
    if old_value != actual_pending_holds:
        logger.warning(
            "Balance reconciliation: paid_pending_holds mismatch detected",
            user_id=user_id,
            cached_value=str(old_value),
            actual_value=str(actual_pending_holds),
            drift=str(old_value - actual_pending_holds),
        )
        balance.paid_pending_holds = actual_pending_holds
        balance.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(balance)
    else:
        logger.info(
            "Balance reconciliation: paid_pending_holds is consistent",
            user_id=user_id,
            value=str(actual_pending_holds),
        )
    
    return balance


async def reconcile_all_balances(db: AsyncSession, user_id: int) -> CreditAccountBalance:
    """
    Full reconciliation of paid_pending_holds AND staking_available pending holds
    by recomputing from the ledger.
    
    Returns the updated balance record.
    """
    # Sum amount_paid for pending usage_hold entries
    paid_result = await db.execute(
        select(func.coalesce(func.sum(CreditLedger.amount_paid), Decimal("0"))).where(
            CreditLedger.user_id == user_id,
            CreditLedger.entry_type == LedgerEntryType.usage_hold,
            CreditLedger.status == LedgerStatus.pending,
        )
    )
    actual_pending_holds_paid = paid_result.scalar() or Decimal("0")
    
    balance = await get_or_create_balance(db, user_id)
    old_paid_holds = balance.paid_pending_holds or Decimal("0")
    
    changed = False
    if old_paid_holds != actual_pending_holds_paid:
        logger.warning(
            "Balance reconciliation: paid_pending_holds mismatch",
            user_id=user_id,
            cached=str(old_paid_holds),
            actual=str(actual_pending_holds_paid),
        )
        balance.paid_pending_holds = actual_pending_holds_paid
        changed = True
    
    if changed:
        balance.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(balance)
        logger.info("Balance reconciliation complete - corrections applied", user_id=user_id)
    else:
        logger.info("Balance reconciliation complete - no corrections needed", user_id=user_id)
    
    return balance


# === Ledger Entry Operations ===

async def get_ledger_entry_by_id(
    db: AsyncSession,
    entry_id: uuid.UUID,
    for_update: bool = False,
) -> Optional[CreditLedger]:
    """
    Get a ledger entry by its ID.
    
    Args:
        for_update: If True, acquires a row-level lock (SELECT ... FOR UPDATE)
                    to prevent concurrent modifications (e.g. double-void).
    """
    query = select(CreditLedger).where(CreditLedger.id == entry_id)
    if for_update:
        query = query.with_for_update()
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_ledger_entry_by_idempotency_key(
    db: AsyncSession, 
    idempotency_key: str
) -> Optional[CreditLedger]:
    """
    Get a ledger entry by idempotency key.
    Used for Stripe/Coinbase purchase deduplication.
    """
    result = await db.execute(
        select(CreditLedger).where(CreditLedger.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


async def get_ledger_entry_by_external_transaction(
    db: AsyncSession,
    external_transaction_id: str,
    payment_source: Optional[str] = None
) -> Optional[CreditLedger]:
    """
    Get a ledger entry by external transaction ID.
    Used for deduplication during webhook processing for any payment provider.
    
    Args:
        external_transaction_id: The provider's primary transaction ID
        payment_source: Optional filter by payment source (e.g., "stripe", "coinbase")
    """
    query = select(CreditLedger).where(
        CreditLedger.external_transaction_id == external_transaction_id
    )
    if payment_source:
        query = query.where(CreditLedger.payment_source == payment_source)
    
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_ledger_entry_by_request_id(
    db: AsyncSession,
    user_id: int,
    request_id: str
) -> Optional[CreditLedger]:
    """
    Get a ledger entry by request_id for a user.
    """
    result = await db.execute(
        select(CreditLedger).where(
            CreditLedger.user_id == user_id,
            CreditLedger.request_id == request_id
        )
    )
    return result.scalar_one_or_none()


async def create_ledger_entry(
    db: AsyncSession,
    user_id: int,
    entry_type: LedgerEntryType,
    status: LedgerStatus,
    amount_paid: Decimal = Decimal("0"),
    amount_staking: Decimal = Decimal("0"),
    entry_id: Optional[uuid.UUID] = None,
    idempotency_key: Optional[str] = None,
    related_entry_id: Optional[uuid.UUID] = None,
    request_id: Optional[str] = None,
    api_key_id: Optional[int] = None,
    model_name: Optional[str] = None,
    model_id: Optional[str] = None,
    endpoint: Optional[str] = None,
    tokens_input: Optional[int] = None,
    tokens_output: Optional[int] = None,
    tokens_total: Optional[int] = None,
    input_price_per_million: Optional[Decimal] = None,
    output_price_per_million: Optional[Decimal] = None,
    failure_code: Optional[str] = None,
    failure_reason: Optional[str] = None,
    description: Optional[str] = None,
    currency: str = "USD",
    # Payment metadata (for any provider)
    payment_source: Optional[str] = None,
    external_transaction_id: Optional[str] = None,
    payment_metadata: Optional[dict] = None,
    auto_commit: bool = True,
) -> CreditLedger:
    """
    Create a new ledger entry.
    
    Args:
        entry_id: Optional pre-generated UUID for the entry. If not provided, one is generated.
        idempotency_key: Optional key for deduplication (used for Stripe/Coinbase purchases).
        payment_source: Payment provider source (e.g., "stripe", "coinbase", "manual").
        external_transaction_id: Primary transaction ID from the payment provider (indexed for lookups).
        payment_metadata: Provider-specific metadata as a dict (stored as JSONB).
            Example for Stripe: {"checkout_session_id": "cs_xxx", "payment_intent_id": "pi_xxx"}
            Example for Coinbase: {"charge_id": "xxx", "charge_code": "xxx"}
        auto_commit: If False, changes are flushed but not committed.
                     Caller is responsible for committing the transaction.
    """
    entry = CreditLedger(
        id=entry_id or uuid.uuid4(),
        user_id=user_id,
        currency=currency,
        status=status,
        entry_type=entry_type,
        amount_paid=amount_paid,
        amount_staking=amount_staking,
        idempotency_key=idempotency_key,
        related_entry_id=related_entry_id,
        request_id=request_id,
        api_key_id=api_key_id,
        model_name=model_name,
        model_id=model_id,
        endpoint=endpoint,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        tokens_total=tokens_total,
        input_price_per_million=input_price_per_million,
        output_price_per_million=output_price_per_million,
        failure_code=failure_code,
        failure_reason=failure_reason,
        description=description,
        # Payment metadata (any provider)
        payment_source=payment_source,
        external_transaction_id=external_transaction_id,
        payment_metadata=payment_metadata,
    )
    
    db.add(entry)
    if auto_commit:
        await db.commit()
        await db.refresh(entry)
    else:
        await db.flush()
    
    logger.info(
        "Created ledger entry",
        user_id=user_id,
        entry_id=str(entry.id),
        entry_type=entry_type.value,
        status=status.value,
        amount_paid=str(amount_paid),
        amount_staking=str(amount_staking),
    )
    
    return entry


async def update_ledger_entry(
    db: AsyncSession,
    entry: CreditLedger,
    status: Optional[LedgerStatus] = None,
    entry_type: Optional[LedgerEntryType] = None,
    amount_paid: Optional[Decimal] = None,
    amount_staking: Optional[Decimal] = None,
    tokens_input: Optional[int] = None,
    tokens_output: Optional[int] = None,
    tokens_total: Optional[int] = None,
    input_price_per_million: Optional[Decimal] = None,
    output_price_per_million: Optional[Decimal] = None,
    model_name: Optional[str] = None,
    model_id: Optional[str] = None,
    endpoint: Optional[str] = None,
    failure_code: Optional[str] = None,
    failure_reason: Optional[str] = None,
    auto_commit: bool = True,
) -> CreditLedger:
    """
    Update an existing ledger entry.
    
    Args:
        auto_commit: If False, changes are flushed but not committed.
                     Caller is responsible for committing the transaction.
    """
    if status is not None:
        entry.status = status
    if entry_type is not None:
        entry.entry_type = entry_type
    if amount_paid is not None:
        entry.amount_paid = amount_paid
    if amount_staking is not None:
        entry.amount_staking = amount_staking
    if tokens_input is not None:
        entry.tokens_input = tokens_input
    if tokens_output is not None:
        entry.tokens_output = tokens_output
    if tokens_total is not None:
        entry.tokens_total = tokens_total
    if input_price_per_million is not None:
        entry.input_price_per_million = input_price_per_million
    if output_price_per_million is not None:
        entry.output_price_per_million = output_price_per_million
    if model_name is not None:
        entry.model_name = model_name
    if model_id is not None:
        entry.model_id = model_id
    if endpoint is not None:
        entry.endpoint = endpoint
    if failure_code is not None:
        entry.failure_code = failure_code
    if failure_reason is not None:
        entry.failure_reason = failure_reason
    
    entry.updated_at = datetime.utcnow()
    
    if auto_commit:
        await db.commit()
        await db.refresh(entry)
    else:
        await db.flush()
    
    return entry


# === Query Operations ===

async def get_transactions(
    db: AsyncSession,
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    entry_type: Optional[LedgerEntryType] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
) -> Tuple[List[CreditLedger], int]:
    """
    Get paginated transactions for a user.
    Returns (entries, total_count).
    """
    # Base query
    query = select(CreditLedger).where(CreditLedger.user_id == user_id)
    count_query = select(func.count(CreditLedger.id)).where(CreditLedger.user_id == user_id)
    
    # Apply filters
    if entry_type is not None:
        query = query.where(CreditLedger.entry_type == entry_type)
        count_query = count_query.where(CreditLedger.entry_type == entry_type)

    normalized_from_date = _normalize_datetime(from_date)
    normalized_to_date = _normalize_datetime(to_date)

    if normalized_from_date is not None:
        query = query.where(CreditLedger.created_at >= normalized_from_date)
        count_query = count_query.where(CreditLedger.created_at >= normalized_from_date)

    if normalized_to_date is not None:
        query = query.where(CreditLedger.created_at <= normalized_to_date)
        count_query = count_query.where(CreditLedger.created_at <= normalized_to_date)
    
    # Order and paginate
    query = query.order_by(CreditLedger.created_at.desc()).offset(offset).limit(limit)
    
    # Execute
    result = await db.execute(query)
    entries = list(result.scalars())
    
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0
    
    return entries, total


async def get_usage_entries(
    db: AsyncSession,
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    model_filter: Optional[str] = None,
) -> Tuple[List[CreditLedger], int]:
    """
    Get paginated usage charge entries for a user.
    Returns (entries, total_count).
    """
    # Base query for posted usage charges only
    base_filter = and_(
        CreditLedger.user_id == user_id,
        CreditLedger.status == LedgerStatus.posted,
        CreditLedger.entry_type == LedgerEntryType.usage_charge,
    )
    
    query = select(CreditLedger).where(base_filter)
    count_query = select(func.count(CreditLedger.id)).where(base_filter)
    
    # Apply optional filters
    normalized_from_date = _normalize_datetime(from_date)
    normalized_to_date = _normalize_datetime(to_date)

    if normalized_from_date is not None:
        query = query.where(CreditLedger.created_at >= normalized_from_date)
        count_query = count_query.where(CreditLedger.created_at >= normalized_from_date)

    if normalized_to_date is not None:
        query = query.where(CreditLedger.created_at <= normalized_to_date)
        count_query = count_query.where(CreditLedger.created_at <= normalized_to_date)
    
    if model_filter is not None:
        query = query.where(CreditLedger.model_name == model_filter)
        count_query = count_query.where(CreditLedger.model_name == model_filter)
    
    # Order and paginate
    query = query.order_by(CreditLedger.created_at.desc()).offset(offset).limit(limit)
    
    # Execute
    result = await db.execute(query)
    entries = list(result.scalars())
    
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0
    
    return entries, total


async def get_monthly_spending(
    db: AsyncSession,
    user_id: int,
    year: int,
    include_refunds: bool = False,
) -> List[Tuple[int, Decimal, int]]:
    """
    Get monthly spending totals for a year.
    Returns list of (month, total_amount, transaction_count) tuples.
    """
    # Define entry types to include
    entry_types = [LedgerEntryType.usage_charge]
    if include_refunds:
        entry_types.append(LedgerEntryType.refund)
    
    # Query for monthly aggregates
    query = (
        select(
            extract('month', CreditLedger.created_at).label('month'),
            func.sum(CreditLedger.amount_paid + CreditLedger.amount_staking).label('total'),
            func.count(CreditLedger.id).label('count'),
        )
        .where(
            CreditLedger.user_id == user_id,
            CreditLedger.status == LedgerStatus.posted,
            CreditLedger.entry_type.in_(entry_types),
            extract('year', CreditLedger.created_at) == year,
        )
        .group_by(extract('month', CreditLedger.created_at))
    )
    
    result = await db.execute(query)
    rows = result.fetchall()
    
    # Convert to list of tuples
    return [(int(row.month), row.total or Decimal("0"), row.count or 0) for row in rows]
