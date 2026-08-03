"""
JSON-file-backed pricing provider implementation.

Loads model pricing from environment-specific JSON files in the models/ directory.
"""

import re
from typing import Optional, List, Dict, TYPE_CHECKING
from decimal import Decimal
from datetime import datetime

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from .types import ModelPricing
from .provider import PricingProvider
from src.core.config_loader import load_model_prices


def _slugify_model_name(model_name: str) -> str:
    """Kebab-slug for pricing lookup (aligned with catalog_name_slug).

    Spaces/underscores → '-', strip vendor prefixes (org/model), keep ':web'.
    Deliberately not difflib — wrong price match is worse than default.
    """
    normalized = (model_name or "").lower().strip()
    if not normalized:
        return ""
    # org/model → model (moonshotai/kimi-k3)
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    normalized = re.sub(r"[\s_]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    for suffix in ["-instruct", "-chat", "-base"]:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return normalized


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
            pricing = ModelPricing(
                model_name=name,
                input_price_per_million=Decimal(prices["input"]),
                output_price_per_million=Decimal(prices["output"]),
                model_id=None,
                currency="USD",
                effective_from=effective_date,
                metadata={"source": "json_config", "version": "1.0"},
            )
            self._pricing_cache[name.lower()] = pricing
            # Also index the slug form so spaced JSON keys / lookups collide less.
            slug = _slugify_model_name(name)
            if slug and slug not in self._pricing_cache:
                self._pricing_cache[slug] = pricing

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
        """Normalize model name for consistent lookup (slug + suffix trim)."""
        return _slugify_model_name(model_name)

    def _fuzzy_match_pricing(self, normalized_name: str) -> Optional[ModelPricing]:
        """
        Conservative substring match for pricing.

        Only matches when a known key is contained in the query (longest wins),
        e.g. "meta-llama-3.3-70b" → "llama-3.3-70b". Does not match the reverse
        (query contained in a longer key) — that mispriced base vs -fast / -pro.
        Does not use difflib near-miss (too risky for money).
        """
        if not normalized_name:
            return None

        best_name: Optional[str] = None
        best_pricing: Optional[ModelPricing] = None
        for known_name, pricing in self._pricing_cache.items():
            if not known_name:
                continue
            if known_name in normalized_name:
                if best_name is None or len(known_name) > len(best_name):
                    best_name = known_name
                    best_pricing = pricing
        return best_pricing

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
