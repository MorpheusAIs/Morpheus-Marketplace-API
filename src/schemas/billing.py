"""
Pydantic schemas for billing/credits API.
"""
from typing import Optional, List, Annotated
from pydantic import BaseModel, Field, ConfigDict, PlainSerializer
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
import uuid


# Custom Decimal type that serializes to string without scientific notation
def _serialize_decimal(value: Decimal) -> str:
    """Serialize Decimal to string, normalizing to remove trailing zeros."""
    if value is None:
        return "0"
    # Normalize removes trailing zeros, then format as fixed-point
    normalized = value.normalize()
    # Handle the case where normalize() returns scientific notation for very small numbers
    if 'E' in str(normalized):
        return f"{value:.8f}".rstrip('0').rstrip('.')
    return str(normalized)


DecimalStr = Annotated[Decimal, PlainSerializer(_serialize_decimal, return_type=str)]


class LedgerStatusEnum(str, Enum):
    """Ledger entry status."""
    pending = "pending"
    posted = "posted"
    voided = "voided"


class LedgerEntryTypeEnum(str, Enum):
    """Ledger entry type."""
    purchase = "purchase"
    staking_refresh = "staking_refresh"
    usage_hold = "usage_hold"
    usage_charge = "usage_charge"
    refund = "refund"
    adjustment = "adjustment"


class SpendingModeEnum(str, Enum):
    """Spending calculation mode."""
    gross = "gross"  # Only usage_charge
    net = "net"  # usage_charge + refunds


# === Balance Schemas ===

class PaidBalanceInfo(BaseModel):
    """Paid bucket balance details."""
    posted_balance: DecimalStr = Field(..., description="Posted paid credits balance")
    pending_holds: DecimalStr = Field(..., description="Pending holds (negative)")
    available: DecimalStr = Field(..., description="Available paid credits (posted + holds)")
    
    model_config = ConfigDict(from_attributes=True)


class StakingBalanceInfo(BaseModel):
    """Staking bucket balance details."""
    daily_amount: DecimalStr = Field(..., description="Configured daily staking allowance")
    refresh_date: Optional[date] = Field(None, description="Last staking refresh date")
    available: DecimalStr = Field(..., description="Currently available staking credits")
    
    model_config = ConfigDict(from_attributes=True)


class BalanceResponse(BaseModel):
    """Response for GET /billing/balance."""
    paid: PaidBalanceInfo
    staking: StakingBalanceInfo
    total_available: DecimalStr = Field(..., description="Total available credits (paid + staking)")
    is_staker: bool = Field(
        default=False,
        description="True if user has linked wallets with active MOR stake.",
    )
    allow_overage: bool = Field(
        default=False,
        description=(
            "Stakers only. When enabled, the system automatically uses your paid Credit Balance "
            "after the Daily Staking Allowance is exhausted, preventing service interruption. "
            "When disabled, requests will fail once staking credits are depleted. "
            "This setting has no effect for non-stakers."
        ),
    )
    currency: str = Field(default="USD", description="Currency code")
    
    model_config = ConfigDict(from_attributes=True)


# === Ledger Entry Schemas ===

class LedgerEntryResponse(BaseModel):
    """Response for a single ledger entry."""
    id: uuid.UUID
    user_id: int
    currency: str
    status: LedgerStatusEnum
    entry_type: LedgerEntryTypeEnum
    amount_paid: DecimalStr
    amount_staking: DecimalStr
    amount_total: DecimalStr
    idempotency_key: Optional[str] = None  # Optional - used for Stripe/Coinbase purchases
    related_entry_id: Optional[uuid.UUID] = None
    
    # Payment source fields
    payment_source: Optional[str] = None  # stripe, coinbase, manual, etc.
    external_transaction_id: Optional[str] = None  # For Stripe: checkout_session_id or invoice_id
    # For Coinbase: charge_id
    # For others: their primary transaction identifier
    payment_metadata: Optional[dict] = None  # JSONB column for provider-specific metadata
    
    # Usage metadata
    request_id: Optional[str] = None
    api_key_id: Optional[int] = None
    model_name: Optional[str] = None
    model_id: Optional[str] = None
    endpoint: Optional[str] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    tokens_total: Optional[int] = None
    input_price_per_million: Optional[DecimalStr] = None
    output_price_per_million: Optional[DecimalStr] = None
    failure_code: Optional[str] = None
    failure_reason: Optional[str] = None
    description: Optional[str] = None
    
    # Timestamps
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class LedgerListResponse(BaseModel):
    """Paginated response for ledger entries."""
    items: List[LedgerEntryResponse]
    total: int
    limit: int
    offset: int
    has_more: bool
    
    model_config = ConfigDict(from_attributes=True)


# === Spending Metrics Schemas ===

class MonthlySpending(BaseModel):
    """Spending for a single month."""
    year: int
    month: int
    amount: DecimalStr = Field(..., description="Spending amount (negative)")
    transaction_count: int = Field(default=0, description="Number of transactions")


class MonthlySpendingResponse(BaseModel):
    """Response for GET /billing/spending."""
    year: int
    mode: SpendingModeEnum
    months: List[MonthlySpending] = Field(default_factory=list, description="12 months of spending data")
    total: DecimalStr = Field(..., description="Total spending for the year")
    currency: str = Field(default="USD")
    
    model_config = ConfigDict(from_attributes=True)


# === Usage List Schemas ===

class UsageEntryResponse(BaseModel):
    """Single usage entry for usage list."""
    id: uuid.UUID
    created_at: datetime
    model_name: Optional[str] = None
    model_id: Optional[str] = None
    endpoint: Optional[str] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    tokens_total: Optional[int] = None
    amount_paid: DecimalStr
    amount_staking: DecimalStr
    amount_total: DecimalStr
    request_id: Optional[str] = None
    api_key_id: Optional[int] = None
    
    model_config = ConfigDict(from_attributes=True)


class UsageListResponse(BaseModel):
    """Paginated response for usage entries."""
    items: List[UsageEntryResponse]
    total: int
    limit: int
    offset: int
    has_more: bool
    
    model_config = ConfigDict(from_attributes=True)


# === Staking Settings Schemas ===

class StakingSettingsRequest(BaseModel):
    """Request for POST /billing/staking/settings."""
    daily_amount: Decimal = Field(..., ge=0, description="Daily staking allowance amount")


class StakingSettingsResponse(BaseModel):
    """Response for staking settings update."""
    daily_amount: DecimalStr
    message: str = "Staking daily amount updated"
    
    model_config = ConfigDict(from_attributes=True)


class OverageSettingsRequest(BaseModel):
    """Request for PUT /billing/settings/overage."""
    allow_overage: bool = Field(
        ...,
        description=(
            "When enabled, the system automatically uses your paid Credit Balance "
            "after the Daily Staking Allowance is exhausted. "
            "When disabled, requests will fail once staking credits are depleted."
        ),
    )


class OverageSettingsResponse(BaseModel):
    """Response for overage settings update."""
    allow_overage: bool
    message: str
    
    model_config = ConfigDict(from_attributes=True)


class StakingRefreshResponse(BaseModel):
    """Response for POST /billing/staking/refresh."""
    success: bool = Field(default=True, description="Whether the sync completed successfully")
    message: str
    
    # Sync summary (from Builders API sync)
    stakers_fetched: Optional[int] = Field(None, description="Number of stakers fetched from Builders API")
    total_wallets: Optional[int] = Field(None, description="Total linked wallets in system")
    wallets_updated: Optional[int] = Field(None, description="Wallets with stake changes")
    users_processed: Optional[int] = Field(None, description="Users with balance updated")
    users_skipped: Optional[int] = Field(None, description="Users already refreshed today")
    users_failed: Optional[int] = Field(None, description="Users that failed to sync")
    duration_seconds: Optional[float] = Field(None, description="Sync duration in seconds")
    
    model_config = ConfigDict(from_attributes=True)


# === Manual Top-up Schemas ===

class ManualTopupRequest(BaseModel):
    """Request for POST /billing/credits/adjust."""
    amount_usd: Decimal = Field(..., description="Amount to adjust in USD (positive to add, negative to subtract)")
    description: Optional[str] = Field(None, max_length=500, description="Optional description/reason for adjustment")
    user_id: Optional[int] = Field(None, description="Target user ID (database primary key). If not provided, adjusts current user's credits.")
    cognito_user_id: Optional[uuid.UUID] = Field(None, description="Target Cognito user ID. Alternative to user_id.")


class ManualTopupResponse(BaseModel):
    """Response for manual credit top-up."""
    ledger_entry_id: uuid.UUID
    amount_added: DecimalStr
    new_paid_balance: DecimalStr
    message: str = "Credits added successfully"
    
    model_config = ConfigDict(from_attributes=True)


# === Internal Service Schemas ===

class UsageHoldRequest(BaseModel):
    """Internal request to create a usage hold."""
    ledger_entry_id: uuid.UUID  # Pre-generated ID for the ledger entry
    request_id: str  # Trace ID for logging/debugging
    estimated_input_tokens: int
    estimated_output_tokens: int = 500  # Default estimate
    api_key_id: Optional[int] = None
    model_name: Optional[str] = None  # Human-readable model name
    model_id: Optional[str] = None  # Hex32 blockchain model identifier
    endpoint: Optional[str] = None


class UsageHoldResponse(BaseModel):
    """Internal response from creating a usage hold."""
    ledger_entry_id: Optional[uuid.UUID] = None
    hold_amount: Optional[DecimalStr] = None
    estimated_cost: Optional[DecimalStr] = None
    available_balance: Optional[DecimalStr] = None
    success: bool
    error: Optional[str] = None


class UsageFinalizeRequest(BaseModel):
    """Internal request to finalize usage."""
    ledger_entry_id: uuid.UUID  # ID of the hold entry to finalize
    tokens_input: int
    tokens_output: int
    tokens_total: int
    model_name: Optional[str] = None  # Human-readable model name for pricing lookup
    model_id: Optional[str] = None  # Hex32 blockchain model ID for pricing lookup
    endpoint: Optional[str] = None


class UsageFinalizeResponse(BaseModel):
    """Internal response from finalizing usage."""
    ledger_entry_id: uuid.UUID
    amount_paid: DecimalStr
    amount_staking: DecimalStr
    amount_total: DecimalStr
    success: bool
    error: Optional[str] = None


class UsageVoidRequest(BaseModel):
    """Internal request to void a usage hold."""
    ledger_entry_id: uuid.UUID  # ID of the hold entry to void
    failure_code: Optional[str] = None
    failure_reason: Optional[str] = None


class UsageVoidResponse(BaseModel):
    """Internal response from voiding a usage hold."""
    ledger_entry_id: uuid.UUID
    voided: bool
    error: Optional[str] = None


class RefundRequest(BaseModel):
    """Internal request to create a refund."""
    request_id: str
    amount: Decimal
    reason: str


class RefundResponse(BaseModel):
    """Internal response from creating a refund."""
    ledger_entry_id: uuid.UUID
    amount_refunded: DecimalStr
    success: bool
    error: Optional[str] = None


# === Rate Limit Multiplier (Admin) ===

class RateLimitMultiplierRequest(BaseModel):
    """Request for POST /billing/rate-limit/multiplier."""
    cognito_user_id: str = Field(..., description="Target user's Cognito ID (UUID)")
    multiplier: float = Field(
        ...,
        gt=0,
        le=100,
        description="Rate limit multiplier (1.0 = default, 2.0 = double limits, 0.5 = half limits)",
    )


class RateLimitMultiplierResponse(BaseModel):
    """Response for rate limit multiplier operations."""
    cognito_user_id: str
    user_id: int
    multiplier: float
    message: str
