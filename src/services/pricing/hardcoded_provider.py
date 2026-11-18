"""
JSON-file-backed pricing provider implementation.

Loads model pricing from environment-specific JSON files in the models/ directory.
"""

import re
from typing import Any, Optional, List, Dict, TYPE_CHECKING
from decimal import Decimal, InvalidOperation
from datetime import datetime

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from .types import ModelPricing, ClipPricing
from .provider import PricingProvider
from src.core.config_loader import load_model_prices
from src.core.logging_config import get_core_logger

logger = get_core_logger()


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


def _clip_price_key(value: Optional[str]) -> str:
    """Exact clip_prices key: trimmed and lowercased, nothing else."""
    return (value or "").strip().lower()


def _parse_price(value: Any) -> Decimal:
    """Parse a non-negative price string; raises ValueError otherwise."""
    price = Decimal(str(value))
    if not price.is_finite() or price < 0:
        raise ValueError(f"invalid price {value!r}")
    return price


def _parse_clip_pricing(key: str, spec: Any) -> ClipPricing:
    """Build ClipPricing from one clip_prices entry; raises on a malformed entry."""
    if not isinstance(spec, dict) or not isinstance(spec.get("per_second"), dict):
        raise ValueError("per_second must be an object")
    per_second = {
        _clip_price_key(resolution): _parse_price(price)
        for resolution, price in spec["per_second"].items()
    }
    if not per_second:
        raise ValueError("per_second is empty")
    audio = spec.get("audio_per_second")
    return ClipPricing(
        model_key=key,
        per_second=per_second,
        audio_per_second=_parse_price(audio) if audio is not None else None,
        currency="USD",
    )


class HardcodedPricingProvider(PricingProvider):
    """
    Pricing provider backed by JSON config files.

    Reads per-model and default prices from models/{env}_model_price.json,
    selected automatically based on the ENVIRONMENT variable.
    """

    def __init__(self):
        self._pricing_cache: Dict[str, ModelPricing] = {}
        self._clip_pricing_cache: Dict[str, ClipPricing] = {}
        self._default_input_price: Decimal = Decimal("2.00")
        self._default_output_price: Decimal = Decimal("8.00")
        self._initialize_pricing()

    def _initialize_pricing(self) -> None:
        """Build the pricing cache from JSON config."""
        config = load_model_prices()
        effective_date = datetime(2024, 12, 1)

        self._default_input_price = Decimal(config.get("default_input_price_per_million", "2.00"))
        self._default_output_price = Decimal(config.get("default_output_price_per_million", "8.00"))

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

        # Video clip prices live in their own top-level key, never under "models".
        # A malformed entry is skipped (that model stays unpriced and fails closed)
        # rather than taking chat pricing down with it.
        clip_prices = config.get("clip_prices") or {}
        if not isinstance(clip_prices, dict):
            logger.error("clip_prices must be an object; no clip is priced", event_type="clip_price_config_invalid")
            clip_prices = {}
        for key, spec in clip_prices.items():
            clip_key = _clip_price_key(key)
            if not clip_key:
                continue
            try:
                self._clip_pricing_cache[clip_key] = _parse_clip_pricing(clip_key, spec)
            except (TypeError, ValueError, InvalidOperation) as e:
                logger.error(
                    "Skipping malformed clip price entry",
                    model_key=clip_key,
                    error=str(e),
                    event_type="clip_price_config_invalid",
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

    async def get_clip_pricing(
        self,
        model_name: Optional[str] = None,
        model_id: Optional[str] = None,
        db: Optional["AsyncSession"] = None,
    ) -> Optional[ClipPricing]:
        """
        Get per-clip pricing by exact key: the 0x model id first, then the
        lowercased model name. No slug, suffix trim or fuzzy match, so a
        "-pro" variant never picks up the base model's price.
        """
        for key in (_clip_price_key(model_id), _clip_price_key(model_name)):
            if key and key in self._clip_pricing_cache:
                return self._clip_pricing_cache[key]
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

    def add_clip_pricing(
        self,
        model_key: str,
        per_second: Dict[str, Decimal],
        audio_per_second: Optional[Decimal] = None,
    ) -> None:
        """Add or update clip pricing for a model name or id (for testing purposes)."""
        key = _clip_price_key(model_key)
        self._clip_pricing_cache[key] = ClipPricing(
            model_key=key,
            per_second={_clip_price_key(r): Decimal(p) for r, p in per_second.items()},
            audio_per_second=Decimal(audio_per_second) if audio_per_second is not None else None,
            currency="USD",
        )
