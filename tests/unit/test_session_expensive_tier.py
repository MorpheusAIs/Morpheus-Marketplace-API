"""Tests for the expensive-model session tier in SessionRoutingService.

Expensive models (lowest rated bid >= a MOR/sec cutoff) open with a shorter
duration to bound the amplified on-chain stake, and use their own idle grace.
The tier is disabled by default (cutoff <= 0) and every decision is best-effort:
a failed / missing price lookup must never block an open, so it falls back to
the global session settings. The proxy-router bid read is mocked here.
"""
import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.core.config import settings
from src.db.models import SessionState
from src.services.session_routing_service import SessionRoutingService


@pytest.fixture
def service():
    return SessionRoutingService()


def _bids_response(bids):
    """A getRatedBids() response object whose .json() yields ``bids``."""
    resp = MagicMock()
    resp.json.return_value = bids
    return resp


def _patch_rated_bids(bids):
    return patch(
        "src.services.session_routing_service.proxy_router_service.getRatedBids",
        new_callable=AsyncMock,
        return_value=_bids_response(bids),
    )


# 1e16 wei = 0.01 MOR/sec (premium), 1e14 wei = 0.0001 MOR/sec (cheap)
PREMIUM_PPS = "10000000000000000"
CHEAP_PPS = "100000000000000"


# ---------------------------------------------------------------------------
# _get_model_min_price_per_second: parsing + best-effort failure
# ---------------------------------------------------------------------------


async def test_min_price_picks_lowest_and_converts_wei_to_mor(service):
    with _patch_rated_bids([{"PricePerSecond": PREMIUM_PPS}, {"PricePerSecond": CHEAP_PPS}]):
        price = await service._get_model_min_price_per_second("0xmodel")
    assert price == pytest.approx(0.0001)


async def test_min_price_supports_dict_bids_envelope(service):
    with _patch_rated_bids({"bids": [{"PricePerSecond": PREMIUM_PPS}]}):
        price = await service._get_model_min_price_per_second("0xmodel")
    assert price == pytest.approx(0.01)


async def test_min_price_none_when_no_bids(service):
    with _patch_rated_bids([]):
        assert await service._get_model_min_price_per_second("0xmodel") is None


async def test_min_price_none_on_lookup_error(service):
    with patch(
        "src.services.session_routing_service.proxy_router_service.getRatedBids",
        new_callable=AsyncMock,
        side_effect=RuntimeError("proxy down"),
    ):
        assert await service._get_model_min_price_per_second("0xmodel") is None


# ---------------------------------------------------------------------------
# _is_expensive_model: cutoff gate, comparison, caching
# ---------------------------------------------------------------------------


async def test_disabled_when_cutoff_zero_skips_price_lookup(service):
    with patch.object(settings, "SESSION_EXPENSIVE_CUTOFF_MOR_PER_SECOND", 0.0), patch(
        "src.services.session_routing_service.proxy_router_service.getRatedBids",
        new_callable=AsyncMock,
    ) as get_bids:
        assert await service._is_expensive_model("0xmodel") is False
        get_bids.assert_not_awaited()


async def test_expensive_when_price_at_or_above_cutoff(service):
    with patch.object(settings, "SESSION_EXPENSIVE_CUTOFF_MOR_PER_SECOND", 0.001), _patch_rated_bids(
        [{"PricePerSecond": PREMIUM_PPS}]
    ):
        assert await service._is_expensive_model("0xmodel") is True


async def test_not_expensive_when_price_below_cutoff(service):
    with patch.object(settings, "SESSION_EXPENSIVE_CUTOFF_MOR_PER_SECOND", 0.001), _patch_rated_bids(
        [{"PricePerSecond": CHEAP_PPS}]
    ):
        assert await service._is_expensive_model("0xmodel") is False


async def test_not_expensive_when_price_unknown(service):
    with patch.object(settings, "SESSION_EXPENSIVE_CUTOFF_MOR_PER_SECOND", 0.001), _patch_rated_bids([]):
        assert await service._is_expensive_model("0xmodel") is False


async def test_decision_is_cached_within_ttl(service):
    with patch.object(settings, "SESSION_EXPENSIVE_CUTOFF_MOR_PER_SECOND", 0.001), patch(
        "src.services.session_routing_service.proxy_router_service.getRatedBids",
        new_callable=AsyncMock,
        return_value=_bids_response([{"PricePerSecond": PREMIUM_PPS}]),
    ) as get_bids:
        assert await service._is_expensive_model("0xmodel") is True
        assert await service._is_expensive_model("0xmodel") is True
        get_bids.assert_awaited_once()


# ---------------------------------------------------------------------------
# open path: duration follows the tier
# ---------------------------------------------------------------------------


def _patch_get_db():
    db = AsyncMock()
    db.add = MagicMock()

    @asynccontextmanager
    async def _fake_get_db():
        yield db

    return patch("src.services.session_routing_service.get_db", _fake_get_db)


async def test_open_uses_expensive_duration_for_expensive_model(service):
    service._is_expensive_model = AsyncMock(return_value=True)
    open_session = AsyncMock(return_value={"sessionID": "0xnew"})

    with _patch_get_db(), patch.object(
        settings, "SESSION_EXPENSIVE_DEFAULT_DURATION_SECONDS", 1200
    ), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        open_session,
    ):
        sid = await service._open_session_for_model(model_id="0xmodel", model_name="m")

    assert sid == "0xnew"
    assert open_session.call_args.kwargs["session_duration"] == 1200


async def test_open_uses_default_duration_for_cheap_model(service):
    service._is_expensive_model = AsyncMock(return_value=False)
    open_session = AsyncMock(return_value={"sessionID": "0xnew"})

    with _patch_get_db(), patch.object(
        settings, "SESSION_DEFAULT_DURATION_SECONDS", 4200
    ), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        open_session,
    ):
        sid = await service._open_session_for_model(model_id="0xmodel", model_name="m")

    assert sid == "0xnew"
    assert open_session.call_args.kwargs["session_duration"] == 4200
