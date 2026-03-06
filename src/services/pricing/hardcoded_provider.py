"""
JSON-file-backed pricing provider implementation.

Loads model pricing from environment-specific JSON files in the models/ directory.
"""

from typing import Optional, List, Dict, TYPE_CHECKING
from decimal import Decimal
from datetime import datetime

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from .types import ModelPricing
from .provider import PricingProvider
from src.core.config_loader import load_model_prices


class HardcodedPricingProvider(PricingProvider):
    """
    Pricing provider backed by JSON config files.

    Reads per-model and default prices from models/{env}_model_price.json,
    selected automatically based on the ENVIRONMENT variable.
    """

    def __init__(self):
        self._pricing_cache: Dict[str, ModelPricing] = {}
        self._default_input_price: Decimal = Decimal("0.50")
        self._default_output_price: Decimal = Decimal("2.00")
        self._initialize_pricing()

    def _initialize_pricing(self) -> None:
        """Build the pricing cache from JSON config."""
        config = load_model_prices()
        effective_date = datetime(2024, 12, 1)

        self._default_input_price = Decimal(config.get("default_input_price_per_million", "0.50"))
        self._default_output_price = Decimal(config.get("default_output_price_per_million", "2.00"))

        for name, prices in config.get("models", {}).items():
            self._pricing_cache[name.lower()] = ModelPricing(
                model_name=name,
                input_price_per_million=Decimal(prices["input"]),
                output_price_per_million=Decimal(prices["output"]),
                model_id=None,
                currency="USD",
                effective_from=effective_date,
                metadata={"source": "json_config", "version": "1.0"},
            )

    @property
    def source_name(self) -> str:
        return "json_config"

    async def get_model_pricing(
        self,
        model_name: Optional[str] = None,
        model_id: Optional[str] = None,
        db: Optional["AsyncSession"] = None,
    ) -> Optional[ModelPricing]:
        """
        Get pricing for a specific model.

        Performs case-insensitive lookup with fuzzy matching.
        """
        if model_name is None:
            return None

        normalized = self._normalize_model_name(model_name)

        if normalized in self._pricing_cache:
            return self._pricing_cache[normalized]

        pricing = self._fuzzy_match_pricing(normalized)
        if pricing:
            return pricing

        return None

    async def get_all_model_pricing(
        self,
        db: Optional["AsyncSession"] = None,
    ) -> List[ModelPricing]:
        return list(self._pricing_cache.values())

    async def get_default_pricing(self) -> ModelPricing:
        return ModelPricing(
            model_name="default",
            input_price_per_million=self._default_input_price,
            output_price_per_million=self._default_output_price,
            model_id=None,
            currency="USD",
            metadata={"source": "json_config_default", "version": "1.0"},
        )

    def _normalize_model_name(self, model_name: str) -> str:
        """
        Normalize model name for consistent lookup.

        Handles case differences and separator/suffix variations.
        """
        normalized = model_name.lower().strip()
        normalized = normalized.replace("_", "-")
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
            if known_name in normalized_name:
                return pricing
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
        """Add or update pricing for a model (for testing purposes)."""
        self._pricing_cache[model_name.lower()] = ModelPricing(
            model_name=model_name,
            input_price_per_million=input_price,
            output_price_per_million=output_price,
            model_id=model_id,
            currency="USD",
            effective_from=datetime.utcnow(),
            metadata={"source": "json_config_dynamic", "version": "1.0"},
        )
