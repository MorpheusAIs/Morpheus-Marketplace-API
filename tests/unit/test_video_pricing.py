"""Clip pricing: price_for, and the exact-key clip_prices lookup.

Both fail closed: a None price makes POST /videos answer 503 instead of
selling an unpriced clip.
"""

import os
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/test_db",
)

import pytest

from src.services.pricing.hardcoded_provider import HardcodedPricingProvider
from src.services.pricing.types import ClipPricing

SEEDANCE_ID = "0x" + "5e" * 32
PER_SECOND = {"480p": Decimal("0.05"), "720p": Decimal("0.10"), "1080p": Decimal("0.25")}
WITH_AUDIO = ClipPricing(model_key="seedance-2.5", per_second=PER_SECOND, audio_per_second=Decimal("0.02"))
NO_AUDIO = ClipPricing(model_key="silent", per_second=PER_SECOND)
FINE = ClipPricing(model_key="fine", per_second={"720p": Decimal("0.000000005")})


@pytest.mark.parametrize(
    "pricing, duration, resolution, audio, expected",
    [
        (WITH_AUDIO, 10, "1080p", True, Decimal("2.70000000")),
        (WITH_AUDIO, 10, "1080p", False, Decimal("2.50000000")),
        (WITH_AUDIO, 5, "720p", False, Decimal("0.50000000")),
        (WITH_AUDIO, 4, "480p", True, Decimal("0.28000000")),
        (WITH_AUDIO, 5, "720P", False, Decimal("0.50000000")),
        # 8 places, half up
        (FINE, 1, "720p", False, Decimal("0.00000001")),
        (WITH_AUDIO, 5, "4k", False, None),
        (WITH_AUDIO, 5, None, False, None),
        (NO_AUDIO, 5, "720p", True, None),
        (NO_AUDIO, 5, "720p", False, Decimal("0.50000000")),
        (WITH_AUDIO, 0, "720p", False, None),
        (WITH_AUDIO, -5, "720p", False, None),
        (WITH_AUDIO, True, "720p", False, None),
    ],
)
def test_price_for(pricing, duration, resolution, audio, expected):
    price = pricing.price_for(duration, resolution, audio)
    assert price == expected
    if expected is not None:
        assert price.as_tuple().exponent == -8


CLIP_PRICES = {
    "seedance-2.5": {"per_second": {"480p": "0.05", "720p": "0.10", "1080p": "0.25"}, "audio_per_second": "0.02"},
    SEEDANCE_ID: {"per_second": {"720p": "0.30"}},
    "negative": {"per_second": {"720p": "-0.10"}},
    "no-rates": {"per_second": {}},
    "not-an-object": {"per_second": "0.10"},
}


@pytest.fixture
def provider():
    config = {"models": {}, "clip_prices": CLIP_PRICES}
    with patch("src.services.pricing.hardcoded_provider.load_model_prices", return_value=config):
        yield HardcodedPricingProvider()


@pytest.mark.parametrize(
    "model_name, model_id, expected_key",
    [
        ("seedance-2.5", None, "seedance-2.5"),
        ("Seedance-2.5", None, "seedance-2.5"),
        (" seedance-2.5 ", None, "seedance-2.5"),
        ("seedance-2.5-pro", None, None),
        ("seedance", None, None),
        ("seedance 2.5", None, None),
        ("unknown-model", None, None),
        (None, SEEDANCE_ID, SEEDANCE_ID),
        (None, SEEDANCE_ID.upper().replace("0X", "0x"), SEEDANCE_ID),
        # The id is tried first, then the name
        ("seedance-2.5", SEEDANCE_ID, SEEDANCE_ID),
        ("seedance-2.5", "0x" + "00" * 32, "seedance-2.5"),
        # A malformed entry is skipped, so the model stays unpriced
        ("negative", None, None),
        ("no-rates", None, None),
        ("not-an-object", None, None),
        (None, None, None),
    ],
)
async def test_clip_pricing_lookup_is_exact(provider, model_name, model_id, expected_key):
    pricing = await provider.get_clip_pricing(model_name=model_name, model_id=model_id)
    if expected_key is None:
        assert pricing is None
    else:
        assert pricing.model_key == expected_key
