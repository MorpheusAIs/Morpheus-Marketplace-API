"""
Hardcoded pricing provider implementation.

This is the initial implementation with static pricing defined in code.
Will be replaced/augmented with database-backed pricing in the future.
"""

from typing import Optional, List, Dict, TYPE_CHECKING
from decimal import Decimal
from datetime import datetime

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from .types import ModelPricing
from .provider import PricingProvider


# Pricing per 1 million tokens in USD
# Format: model_name -> (input_price, output_price)

HARDCODED_PRICING: Dict[str, tuple[str, str]] = {
    # Kimi models
    "kimi-k2.5": ("0.60", "3.00"),
    "kimi-k2-thinking": ("0.60", "3.00"),

    # GLM models
    "glm-4.7": ("0.50", "2.25"),
    "glm-4.7-thinking": ("0.50", "2.25"),
    "glm-4.7-flash": ("0.13", "0.50"),

    # Qwen models
    "qwen3-235b": ("0.40", "3.00"),
    "qwen-3-235b": ("0.40", "3.00"),  # Alternative format
    "qwen3-coder-480b-a35b-instruct": ("0.70", "2.80"),
    "qwen3-coder-480b-a35b": ("0.70", "2.80"),  # Without -instruct suffix
    "qwen3-next-80b": ("0.25", "1.75"),
    "qwen3-4b": ("0.05", "0.15"),
    "qwen-3-4b": ("0.05", "0.15"),  # Alternative format

    # GPT OSS models
    "gpt-oss-120b": ("0.07", "0.28"),

    # Hermes / Llama models
    "hermes-3-llama-3.1-405b": ("1.00", "2.75"),
    "llama-3.3-70b": ("0.70", "2.50"),
    "llama-3-3-70b": ("0.70", "2.50"),  # Alternative format
    "llama-3.2-3b": ("0.10", "0.35"),
    "llama-3-2-3b": ("0.10", "0.35"),  # Alternative format

    # Mistral models
    "mistral-31-24b": ("0.50", "2.00"),
    "mistral-small-24b": ("0.50", "2.00"),  # Alternative format

    # Venice models
    "venice-uncensored": ("0.20", "0.90"),

    # Embedding models
    "text-embedding-bge-m3": ("0.10", "0.50"),
}

# Default pricing for unknown models (conservative high estimate)
DEFAULT_INPUT_PRICE = Decimal("0.50")
DEFAULT_OUTPUT_PRICE = Decimal("2.00")


class HardcodedPricingProvider(PricingProvider):
    """
    Pricing provider with statically defined prices.
    
    This implementation doesn't require database access and returns
    immediately. Useful for development, testing, and as a fallback.
    """
    
    def __init__(self):
        """Initialize with hardcoded pricing data."""
        self._pricing_cache: Dict[str, ModelPricing] = {}
        self._initialize_pricing()
    
    def _initialize_pricing(self) -> None:
        """Build the pricing cache from hardcoded data."""
        effective_date = datetime(2024, 12, 1)  # Pricing effective date
        
        for name, (input_price, output_price) in HARDCODED_PRICING.items():
            self._pricing_cache[name.lower()] = ModelPricing(
                model_name=name,
                input_price_per_million=Decimal(input_price),
                output_price_per_million=Decimal(output_price),
                model_id=None,  # Hex32 ID not available in hardcoded pricing
                currency="USD",
                effective_from=effective_date,
                metadata={"source": "hardcoded", "version": "1.0"},
            )
    
    @property
    def source_name(self) -> str:
        """Return the source name for this provider."""
        return "hardcoded"
    
    async def get_model_pricing(
        self,
        model_name: Optional[str] = None,
        model_id: Optional[str] = None,
        db: Optional["AsyncSession"] = None,
    ) -> Optional[ModelPricing]:
        """
        Get pricing for a specific model.
        
        Hardcoded provider only supports lookup by model_name.
        model_id (hex32) is ignored in this implementation but will be
        used by future DatabasePricingProvider.
        
        Performs case-insensitive lookup with fuzzy matching.
        """
        if model_name is None:
            # Hardcoded provider requires model_name
            return None
        
        normalized = self._normalize_model_name(model_name)
        
        # Direct lookup
        if normalized in self._pricing_cache:
            return self._pricing_cache[normalized]
        
        # Try fuzzy matching (handles variations like "llama-3.3-70b-instruct")
        pricing = self._fuzzy_match_pricing(normalized)
        if pricing:
            return pricing
        
        return None
    
    async def get_all_model_pricing(
        self,
        db: Optional["AsyncSession"] = None,
    ) -> List[ModelPricing]:
        """Get pricing for all available models."""
        return list(self._pricing_cache.values())
    
    async def get_default_pricing(self) -> ModelPricing:
        """Get default pricing for unknown models."""
        return ModelPricing(
            model_name="default",
            input_price_per_million=DEFAULT_INPUT_PRICE,
            output_price_per_million=DEFAULT_OUTPUT_PRICE,
            model_id=None,
            currency="USD",
            metadata={"source": "hardcoded_default", "version": "1.0"},
        )
    
    def _normalize_model_name(self, model_name: str) -> str:
        """
        Normalize model name for consistent lookup.
        
        Handles variations like:
        - Case differences: "Llama-3.3-70B" -> "llama-3.3-70b"
        - Separator differences: "llama_3_3_70b" -> "llama-3-3-70b"
        """
        normalized = model_name.lower().strip()
        # Normalize separators
        normalized = normalized.replace("_", "-")
        # Remove common suffixes that don't affect pricing
        for suffix in ["-instruct", "-chat", "-base"]:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]
        return normalized
    
    def _fuzzy_match_pricing(self, normalized_name: str) -> Optional[ModelPricing]:
        """
        Attempt fuzzy matching for model names.
        
        Matches models with variations like:
        - "llama-3.3-70b-instruct" -> matches "llama-3.3-70b"
        - "meta-llama-3.3-70b" -> matches "llama-3.3-70b"
        """
        for known_name, pricing in self._pricing_cache.items():
            # Check if known name is contained in the provided name
            if known_name in normalized_name:
                return pricing
            # Check if provided name is contained in known name
            if normalized_name in known_name:
                return pricing
        return None
    
    def add_pricing(
        self,
        model_name: str,
        input_price: Decimal,
        output_price: Decimal,
        model_id: Optional[str] = None,
    ) -> None:
        """
        Add or update pricing for a model (for testing purposes).
        
        In production, pricing should be managed through proper channels
        (database or configuration).
        """
        self._pricing_cache[model_name.lower()] = ModelPricing(
            model_name=model_name,
            input_price_per_million=input_price,
            output_price_per_million=output_price,
            model_id=model_id,
            currency="USD",
            effective_from=datetime.utcnow(),
            metadata={"source": "hardcoded_dynamic", "version": "1.0"},
        )

