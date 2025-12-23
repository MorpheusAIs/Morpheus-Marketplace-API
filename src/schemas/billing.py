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
    idempotency_key: str
    related_entry_id: Optional[uuid.UUID] = None
    
    # Usage metadata
    request_id: Optional[str] = None
    api_key_id: Optional[int] = None
    model: Optional[str] = None
    endpoint: Optional[str] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    tokens_total: Optional[int] = None
    price_per_input_token: Optional[DecimalStr] = None
    price_per_output_token: Optional[DecimalStr] = None
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
    model: Optional[str] = None
    endpoint: Optional[str] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    tokens_total: Optional[int] = None
    amount_paid: DecimalStr
    amount_staking: DecimalStr
    amount_total: DecimalStr
    request_id: Optional[str] = None
    
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


class StakingRefreshResponse(BaseModel):
    """Response for POST /billing/staking/refresh."""
    credits_added: DecimalStr
    new_balance: StakingBalanceInfo
    already_refreshed: bool = Field(default=False, description="True if already refreshed today")
    message: str
    
    model_config = ConfigDict(from_attributes=True)


# === Manual Top-up Schemas ===

class ManualTopupRequest(BaseModel):
    """Request for POST /billing/credits/adjust."""
    amount_usd: Decimal = Field(..., description="Amount to adjust in USD (positive to add, negative to subtract)")
    description: Optional[str] = Field(None, max_length=500, description="Optional description/reason for adjustment")


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
    request_id: str
    estimated_max_cost: Decimal
    api_key_id: Optional[int] = None
    model_name: Optional[str] = None  # Human-readable model name
    model_id: Optional[str] = None  # Hex32 blockchain model identifier
    endpoint: Optional[str] = None


class UsageHoldResponse(BaseModel):
    """Internal response from creating a usage hold."""
    ledger_entry_id: uuid.UUID
    hold_amount: DecimalStr
    success: bool
    error: Optional[str] = None


class UsageFinalizeRequest(BaseModel):
    """Internal request to finalize usage."""
    request_id: str
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
    request_id: str
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

