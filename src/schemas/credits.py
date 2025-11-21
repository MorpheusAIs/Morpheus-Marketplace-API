"""
Pydantic schemas for API Credits
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, date


# Balance Schemas
class BalanceResponse(BaseModel):
    balance: Decimal = Field(..., description="Current API credit balance in USD")
    staking_balance: Decimal = Field(..., description="Balance from staking rewards")
    staking_daily_amount: Decimal = Field(..., description="Daily staking refresh amount")
    staking_refresh_date: Optional[date] = Field(None, description="Last staking refresh date")
    total_earned: Decimal = Field(..., description="Lifetime credits earned")
    total_spent: Decimal = Field(..., description="Lifetime credits spent")
    total_refunded: Decimal = Field(..., description="Lifetime credits refunded")
    last_updated: datetime = Field(..., description="Last balance update timestamp")

    class Config:
        from_attributes = True


# Transaction Schemas
class TransactionResponse(BaseModel):
    id: str = Field(..., description="Transaction ID")
    type: str = Field(..., description="Transaction type")
    amount: Decimal = Field(..., description="Transaction amount (negative for deductions)")
    balance_after: Decimal = Field(..., description="Balance after transaction")
    payment_method: Optional[str] = Field(None, description="Payment method")
    payment_id: Optional[str] = Field(None, description="External payment ID")
    request_id: Optional[str] = Field(None, description="Associated request ID")
    model: Optional[str] = Field(None, description="Model used")
    tokens_input: Optional[int] = Field(None, description="Input tokens")
    tokens_output: Optional[int] = Field(None, description="Output tokens")
    tokens_total: Optional[int] = Field(None, description="Total tokens")
    price_per_input_token: Optional[Decimal] = Field(None, description="Price per input token")
    price_per_output_token: Optional[Decimal] = Field(None, description="Price per output token")
    description: Optional[str] = Field(None, description="Transaction description")
    created_at: datetime = Field(..., description="Transaction timestamp")

    class Config:
        from_attributes = True


class TransactionListResponse(BaseModel):
    transactions: List[TransactionResponse] = Field(..., description="List of transactions")
    limit: int = Field(..., description="Query limit")
    offset: int = Field(..., description="Query offset")
    total: int = Field(..., description="Total transactions returned")


# Spending Schemas
class SpendingMetricsResponse(BaseModel):
    year: int = Field(..., description="Year")
    month: int = Field(..., description="Month (1-12)")
    total_spending: Decimal = Field(..., description="Total spending for the month")
    currency: str = Field("USD", description="Currency")


# Purchase Schemas - Stripe
class StripePurchaseRequest(BaseModel):
    amount_usd: float = Field(..., ge=5.0, description="Amount in USD (minimum $5)")
    payment_method_id: Optional[str] = Field(None, description="Stripe payment method ID")


class StripePurchaseResponse(BaseModel):
    payment_intent_id: str = Field(..., description="Stripe payment intent ID")
    client_secret: str = Field(..., description="Client secret for confirming payment")
    credits_amount: Decimal = Field(..., description="Credits to be added")
    amount_usd: Decimal = Field(..., description="Amount in USD")


# Purchase Schemas - Coinbase
class CoinbasePurchaseRequest(BaseModel):
    amount_usd: float = Field(..., ge=5.0, description="Amount in USD (minimum $5)")
    crypto_currency: str = Field("USDC", description="Cryptocurrency (BTC, ETH, USDC, etc.)")


class CoinbasePurchaseResponse(BaseModel):
    charge_id: str = Field(..., description="Coinbase charge ID")
    hosted_url: str = Field(..., description="URL to complete payment")
    credits_amount: Decimal = Field(..., description="Credits to be added")
    amount_usd: Decimal = Field(..., description="Amount in USD")


# Purchase Schemas - MOR
class MORPurchaseRequest(BaseModel):
    mor_amount: float = Field(..., gt=0, description="Amount of MOR tokens")
    transaction_hash: str = Field(..., description="Transaction hash of MOR transfer")


class MORPurchaseResponse(BaseModel):
    credits_amount: Decimal = Field(..., description="Credits added")
    mor_amount: Decimal = Field(..., description="MOR tokens spent")
    mor_price_usd: Decimal = Field(..., description="MOR price in USD")
    transaction_hash: str = Field(..., description="Transaction hash")


# Staking Schemas
class StakingSettingsRequest(BaseModel):
    daily_amount: Decimal = Field(..., ge=0, description="Daily credit amount from staking")


class StakingSettingsResponse(BaseModel):
    daily_amount: Decimal = Field(..., description="Daily credit amount")
    staking_balance: Decimal = Field(..., description="Current staking balance")
    last_refresh_date: Optional[date] = Field(None, description="Last refresh date")


class StakingRefreshResponse(BaseModel):
    credits_added: Decimal = Field(..., description="Credits added")
    new_balance: Decimal = Field(..., description="New total balance")
    refresh_date: date = Field(..., description="Refresh date")
