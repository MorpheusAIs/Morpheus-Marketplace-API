"""
Credit ledger and account balance models for the billing system.
"""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, TEXT, Enum, Numeric, Date, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
import uuid

from .base import Base


class LedgerStatus(enum.Enum):
    """Enum for ledger entry status."""
    pending = "pending"
    posted = "posted"
    voided = "voided"


class LedgerEntryType(enum.Enum):
    """Enum for ledger entry type."""
    purchase = "purchase"
    staking_refresh = "staking_refresh"
    usage_hold = "usage_hold"
    usage_charge = "usage_charge"
    refund = "refund"
    adjustment = "adjustment"


class CreditLedger(Base):
    """
    Single source of truth for all credit movements and usage charges.
    Supports split amounts between paid and staking buckets.
    """
    __tablename__ = "credits_ledger"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    currency = Column(String(10), nullable=False, default="USD")
    status = Column(Enum(LedgerStatus, name='ledger_status'), nullable=False)
    entry_type = Column(Enum(LedgerEntryType, name='ledger_entry_type'), nullable=False)
    
    # Split amounts (negative = charge/debit, positive = credit)
    amount_paid = Column(Numeric(20, 8), nullable=False, default=0)
    amount_staking = Column(Numeric(20, 8), nullable=False, default=0)
    
    # Idempotency (optional - used for Stripe/Coinbase purchases)
    idempotency_key = Column(TEXT, nullable=True, unique=True)
    related_entry_id = Column(UUID(as_uuid=True), ForeignKey("credits_ledger.id", ondelete="SET NULL"), nullable=True)
    
    # Payment source metadata (for purchases from any provider)
    payment_source = Column(String(50), nullable=True)  # e.g., "stripe", "coinbase", "manual"
    external_transaction_id = Column(String(255), nullable=True, index=True)  # Primary transaction ID for lookups
    payment_metadata = Column(JSONB, nullable=True)  # Provider-specific data (flexible schema)
    
    # Usage metadata (nullable for non-usage entries)
    request_id = Column(TEXT, nullable=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True)
    model_name = Column(TEXT, nullable=True)  # Human-readable model name
    model_id = Column(String(66), nullable=True)  # Hex32 blockchain model identifier
    endpoint = Column(TEXT, nullable=True)
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    tokens_total = Column(Integer, nullable=True)
    input_price_per_million = Column(Numeric(20, 8), nullable=True)
    output_price_per_million = Column(Numeric(20, 8), nullable=True)
    failure_code = Column(TEXT, nullable=True)
    failure_reason = Column(TEXT, nullable=True)
    description = Column(TEXT, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    user = relationship("User")
    api_key = relationship("APIKey")
    related_entry = relationship("CreditLedger", remote_side=[id])
    
    @property
    def amount_total(self):
        """Compute total amount from paid + staking."""
        return (self.amount_paid or 0) + (self.amount_staking or 0)


class CreditAccountBalance(Base):
    """
    Cache table for account credit balances.
    Keyed by user_id (user IS the account in this codebase).
    """
    __tablename__ = "credit_account_balances"
    
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    
    # Paid bucket
    paid_posted_balance = Column(Numeric(20, 8), nullable=False, default=0)
    paid_pending_holds = Column(Numeric(20, 8), nullable=False, default=0)  # Typically negative
    
    # Staking bucket
    staking_daily_amount = Column(Numeric(20, 8), nullable=False, default=0)
    staking_refresh_date = Column(Date, nullable=True)
    staking_available = Column(Numeric(20, 8), nullable=False, default=0)
    
    # Staker flag: cached from wallet links, set instantly on link/unlink and during daily sync
    is_staker = Column(Boolean, nullable=False, default=False, server_default="false")
    
    # Overage setting (stakers only): when True, paid balance is used after staking is exhausted
    allow_overage = Column(Boolean, nullable=False, default=False, server_default="false")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    user = relationship("User")
    
    @property
    def paid_available(self):
        """Available paid credits = posted - holds (holds are negative, so this adds)."""
        return (self.paid_posted_balance or 0) + (self.paid_pending_holds or 0)
    
    @property
    def total_available(self):
        """Total available credits across all buckets."""
        return self.paid_available + (self.staking_available or 0)

