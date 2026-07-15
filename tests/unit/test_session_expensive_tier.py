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
from datetime import datetime, timedelta, timezone
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


def _rated(*pps_values):
    """Build a /bids/rated response envelope (price nested under "Bid").

    Mirrors the real proxy-router shape:
    {"bids": [{"ID": "0x..", "Bid": {"PricePerSecond": ...}, "Score": ..}]}.
    """
    return {
        "bids": [
            {
                "ID": "0x0000000000000000000000000000000000000000000000000000000000000000",
                "Bid": {"Id": "0xbid", "PricePerSecond": pps, "Provider": "0xprov"},
                "Score": 65.0,
            }
            for pps in pps_values
        ]
    }


# ---------------------------------------------------------------------------
# _get_model_min_price_per_second: parsing + best-effort failure
# ---------------------------------------------------------------------------


async def test_min_price_picks_lowest_and_converts_wei_to_mor(service):
    with _patch_rated_bids(_rated(PREMIUM_PPS, CHEAP_PPS)):
        price = await service._get_model_min_price_per_second("0xmodel")
    assert price == pytest.approx(0.0001)


async def test_min_price_parses_real_rated_envelope(service):
    """Regression: /bids/rated nests PricePerSecond under "Bid" (not top level)."""
    with _patch_rated_bids(_rated(PREMIUM_PPS)):
        price = await service._get_model_min_price_per_second("0xmodel")
    assert price == pytest.approx(0.01)


async def test_min_price_supports_flat_bids_fallback(service):
    """Older/flat shape (PricePerSecond at the top level) still parses."""
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
        _rated(PREMIUM_PPS)
    ):
        assert await service._is_expensive_model("0xmodel") is True


async def test_not_expensive_when_price_below_cutoff(service):
    with patch.object(settings, "SESSION_EXPENSIVE_CUTOFF_MOR_PER_SECOND", 0.001), _patch_rated_bids(
        _rated(CHEAP_PPS)
    ):
        assert await service._is_expensive_model("0xmodel") is False


async def test_not_expensive_when_price_unknown(service):
    with patch.object(settings, "SESSION_EXPENSIVE_CUTOFF_MOR_PER_SECOND", 0.001), _patch_rated_bids([]):
        assert await service._is_expensive_model("0xmodel") is False


async def test_decision_is_cached_within_ttl(service):
    with patch.object(settings, "SESSION_EXPENSIVE_CUTOFF_MOR_PER_SECOND", 0.001), patch(
        "src.services.session_routing_service.proxy_router_service.getRatedBids",
        new_callable=AsyncMock,
        return_value=_bids_response(_rated(PREMIUM_PPS)),
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


PROVIDER_ADDR = "0xAbC1234567890abcdef1234567890abcdef12345"


def _patch_session_status(ends_at=None, side_effect=None, provider=PROVIDER_ADDR):
    """Patch getSessionStatus to return an on-chain endsAt (Go-style keys)."""
    kwargs = {}
    if side_effect is not None:
        kwargs["side_effect"] = side_effect
    else:
        kwargs["return_value"] = {"session": {"Id": "0xnew", "EndsAt": ends_at, "Provider": provider}}
    return patch(
        "src.services.session_routing_service.proxy_router_service.getSessionStatus",
        new_callable=AsyncMock,
        **kwargs,
    )


async def test_open_uses_expensive_duration_for_expensive_model(service):
    service._is_expensive_model = AsyncMock(return_value=True)
    open_session = AsyncMock(return_value={"sessionID": "0xnew"})

    with _patch_get_db(), patch.object(
        settings, "SESSION_EXPENSIVE_DEFAULT_DURATION_SECONDS", 1200
    ), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        open_session,
    ), _patch_session_status(ends_at=2_000_000_000):
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
    ), _patch_session_status(ends_at=2_000_000_000):
        sid = await service._open_session_for_model(model_id="0xmodel", model_name="m")

    assert sid == "0xnew"
    assert open_session.call_args.kwargs["session_duration"] == 4200


# ---------------------------------------------------------------------------
# expires_at anchoring: align to the on-chain endsAt (+ buffer), not a
# call-start estimate, so cleanup closes land AT/AFTER endsAt (late close ->
# full stake straight to the wallet, no userStakesOnHold / housekeeping).
# ---------------------------------------------------------------------------


def test_parse_onchain_ends_at_go_style_keys(service):
    assert service._parse_onchain_ends_at({"session": {"EndsAt": 1783364629}}) == 1783364629


def test_parse_onchain_ends_at_rejects_zero_and_missing(service):
    assert service._parse_onchain_ends_at({"session": {"EndsAt": 0}}) is None
    assert service._parse_onchain_ends_at({"session": {}}) is None
    assert service._parse_onchain_ends_at({}) is None
    assert service._parse_onchain_ends_at(None) is None


def test_parse_provider_address_go_style_keys(service):
    assert service._parse_provider_address(
        {"session": {"Provider": PROVIDER_ADDR}}
    ) == PROVIDER_ADDR


def test_parse_provider_address_rejects_invalid(service):
    assert service._parse_provider_address({"session": {"Provider": "not-an-addr"}}) is None
    assert service._parse_provider_address({"session": {"Provider": 123}}) is None
    assert service._parse_provider_address({"session": {}}) is None
    assert service._parse_provider_address({}) is None
    assert service._parse_provider_address(None) is None


async def test_open_stores_provider_address(service):
    """The provider serving the new session is persisted for later failover."""
    service._is_expensive_model = AsyncMock(return_value=False)
    open_session = AsyncMock(return_value={"sessionID": "0xnew"})

    captured = {}
    db = AsyncMock()
    db.add = MagicMock(side_effect=lambda row: captured.update(provider=row.provider_address))

    @asynccontextmanager
    async def _fake_get_db():
        yield db

    with patch("src.services.session_routing_service.get_db", _fake_get_db), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        open_session,
    ), _patch_session_status(ends_at=2_000_000_000):
        await service._open_session_for_model(model_id="0xmodel", model_name="m")

    assert captured["provider"] == PROVIDER_ADDR


async def test_open_passes_omit_provider_to_proxy(service):
    """omit_provider flows through to the proxy-router openSession call."""
    service._is_expensive_model = AsyncMock(return_value=False)
    open_session = AsyncMock(return_value={"sessionID": "0xnew"})

    with _patch_get_db(), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        open_session,
    ), _patch_session_status(ends_at=2_000_000_000):
        await service._open_session_for_model(
            model_id="0xmodel", model_name="m", omit_provider=PROVIDER_ADDR
        )

    assert open_session.call_args.kwargs["omit_provider"] == PROVIDER_ADDR


async def test_open_anchors_expires_at_to_onchain_ends_at(service):
    """expires_at is derived from the on-chain endsAt + buffer, not the estimate."""
    service._is_expensive_model = AsyncMock(return_value=False)
    open_session = AsyncMock(return_value={"sessionID": "0xnew"})
    ends_at = 2_000_000_000  # on-chain endsAt (unix seconds)

    captured = {}
    db = AsyncMock()
    db.add = MagicMock(side_effect=lambda row: captured.update(expires_at=row.expires_at))

    @asynccontextmanager
    async def _fake_get_db():
        yield db

    with patch("src.services.session_routing_service.get_db", _fake_get_db), patch.object(
        settings, "SESSION_DEFAULT_DURATION_SECONDS", 4200
    ), patch.object(settings, "SESSION_EXPIRY_BUFFER_SECONDS", 60), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        open_session,
    ), _patch_session_status(ends_at=ends_at):
        await service._open_session_for_model(model_id="0xmodel", model_name="m")

    expected = datetime.fromtimestamp(ends_at, tz=timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=60
    )
    assert captured["expires_at"] == expected


async def test_open_falls_back_to_estimate_when_status_read_fails(service):
    """A failed endsAt read must not block the open; fall back to the estimate."""
    service._is_expensive_model = AsyncMock(return_value=False)
    open_session = AsyncMock(return_value={"sessionID": "0xnew"})

    captured = {}
    db = AsyncMock()
    db.add = MagicMock(side_effect=lambda row: captured.update(expires_at=row.expires_at))

    @asynccontextmanager
    async def _fake_get_db():
        yield db

    before = datetime.now(timezone.utc).replace(tzinfo=None)
    with patch("src.services.session_routing_service.get_db", _fake_get_db), patch.object(
        settings, "SESSION_DEFAULT_DURATION_SECONDS", 4200
    ), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        open_session,
    ), _patch_session_status(side_effect=RuntimeError("proxy down")):
        sid = await service._open_session_for_model(model_id="0xmodel", model_name="m")

    assert sid == "0xnew"
    # Fallback estimate ~ now + duration (far below the year-2033 on-chain value).
    assert captured["expires_at"] >= before + timedelta(seconds=4200)
    assert captured["expires_at"] < before + timedelta(seconds=4200 + 3600)
