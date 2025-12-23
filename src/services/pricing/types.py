"""
Type definitions for the pricing module.

All monetary values use Decimal for precision.
Prices are per 1 million tokens (standard industry convention).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional
from datetime import datetime


@dataclass(frozen=True)
class ModelPricing:
    """
    Pricing configuration for a specific model.
    
    Attributes:
        model_name: Human-readable model name (e.g., "llama-3.3-70b")
        model_id: Hex32 blockchain model identifier (optional, for dynamic pricing)
        input_price_per_million: Price per 1M input tokens in USD
        output_price_per_million: Price per 1M output tokens in USD
        currency: Currency code (default: "USD")
        effective_from: When this pricing became effective
        metadata: Optional additional pricing metadata
    """
    model_name: str
    input_price_per_million: Decimal
    output_price_per_million: Decimal
    model_id: Optional[str] = None  # Hex32 identifier for dynamic pricing
    currency: str = "USD"
    effective_from: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)
    
    def calculate_input_cost(self, tokens: int) -> Decimal:
        """Calculate cost for input tokens."""
        return (Decimal(tokens) / Decimal("1000000")) * self.input_price_per_million
    
    def calculate_output_cost(self, tokens: int) -> Decimal:
        """Calculate cost for output tokens."""
        return (Decimal(tokens) / Decimal("1000000")) * self.output_price_per_million
    
    def calculate_total_cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        """Calculate total cost for both input and output tokens."""
        return self.calculate_input_cost(input_tokens) + self.calculate_output_cost(output_tokens)


@dataclass(frozen=True)
class UsageEstimate:
    """
    Estimated cost before making a request.
    
    Attributes:
        model_name: Human-readable model name
        model_id: Hex32 blockchain model identifier (if available)
        estimated_input_tokens: Estimated input token count
        estimated_output_tokens: Estimated output token count
        estimated_input_cost: Estimated cost for input tokens
        estimated_output_cost: Estimated cost for output tokens
        estimated_total_cost: Total estimated cost
        currency: Currency code
        confidence: Confidence level of estimate (0.0 - 1.0)
        pricing_source: Source of pricing data (e.g., "hardcoded", "database")
    """
    model_name: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_input_cost: Decimal
    estimated_output_cost: Decimal
    estimated_total_cost: Decimal
    model_id: Optional[str] = None
    currency: str = "USD"
    confidence: float = 1.0
    pricing_source: str = "unknown"
    
    @property
    def is_estimated(self) -> bool:
        """Returns True as this is always an estimate."""
        return True


@dataclass(frozen=True)
class UsageCost:
    """
    Actual calculated cost after request completion.
    
    Attributes:
        model_name: Human-readable model name
        model_id: Hex32 blockchain model identifier (if available)
        input_tokens: Actual input token count
        output_tokens: Actual output token count
        input_cost: Cost for input tokens
        output_cost: Cost for output tokens
        total_cost: Total cost
        currency: Currency code
        pricing_source: Source of pricing data
        calculated_at: Timestamp of calculation
    """
    model_name: str
    input_tokens: int
    output_tokens: int
    input_cost: Decimal
    output_cost: Decimal
    total_cost: Decimal
    model_id: Optional[str] = None
    currency: str = "USD"
    pricing_source: str = "unknown"
    calculated_at: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def is_estimated(self) -> bool:
        """Returns False as this is actual cost."""
        return False

