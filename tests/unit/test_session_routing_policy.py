"""Unit tests for SESSION_ROUTING_POLICY_JSON parsing + cheapest-bid fuse."""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.core.session_routing_policy import (
    parse_session_routing_policy,
    reset_session_routing_policy_cache,
)
from src.services.session_routing_service import (
    SessionOpenError,
    SessionRoutingService,
    SessionStakeFuseError,
)


def test_parse_policy_aliases_preferences_deny_stake():
    raw = json.dumps(
        {
            "aliases": {"Claude Opus 4.8": "Claude Opus 4.7", "kimi-k2.5": "Kimi K3"},
            "preferences": {"gpt-5.4": "0x95980c8a9ea2a7542e3fd9857df3932443e45ff9f6cdb2cf332cd3221ed16edb"},
            "deny": {"names": ["claude-haiku-4.5", "GLM-4.7-flash"], "ids": ["0xdead"]},
            "max_stake_mor": 700,
        }
    )
    policy = parse_session_routing_policy(raw)
    assert policy.alias_target("claude opus 4.8") == "Claude Opus 4.7"
    assert policy.preference_target("GPT-5.4").startswith("0x95980")
    assert policy.is_denied_name("claude-haiku-4.5")
    assert policy.is_denied_name("glm-4.7-flash")
    assert policy.is_denied_id("0xdead")
    assert policy.max_stake_mor == 700


def test_parse_policy_invalid_json_empty():
    assert parse_session_routing_policy("{not-json").max_stake_mor == 0
    assert parse_session_routing_policy("").aliases == {}


@pytest.fixture
def service():
    reset_session_routing_policy_cache()
    return SessionRoutingService()


def _patch_cheapest(healthy_ids, rated_resp, *, max_stake=700):
    """Common patches for cheapest-bid selection + active healthy filter."""
    return (
        patch(
            "src.services.session_routing_service.get_session_routing_policy"
        ),
        patch(
            "src.services.session_routing_service.proxy_router_service.getRatedBids",
            new=AsyncMock(return_value=rated_resp),
        ),
        patch(
            "src.services.session_routing_service.direct_model_service.get_healthy_bid_ids_for_model",
            new=AsyncMock(return_value={b.lower() for b in healthy_ids}),
        ),
        patch("src.services.session_routing_service.settings"),
    )


@pytest.mark.asyncio
async def test_cheapest_bid_under_fuse_picks_low_pps(service):
    bids = {
        "bids": [
            {
                "ID": "0xbid_hi",
                "Score": 9.0,
                "Bid": {
                    "Id": "0xbid_hi",
                    "Provider": "0xprovhi",
                    "PricePerSecond": str(int(0.002 * 1e18)),
                },
            },
            {
                "ID": "0xbid_lo",
                "Score": 1.0,
                "Bid": {
                    "Id": "0xbid_lo",
                    "Provider": "0xprovlo",
                    "PricePerSecond": str(int(0.0003 * 1e18)),
                },
            },
        ]
    }
    resp = MagicMock()
    resp.json.return_value = bids
    p_policy, p_rated, p_healthy, p_settings = _patch_cheapest(
        {"0xbid_hi", "0xbid_lo"}, resp
    )

    with p_policy as mock_policy, p_rated, p_healthy, p_settings as mock_settings:
        mock_policy.return_value.max_stake_mor = 700
        mock_settings.SESSION_STAKE_FACTOR = 338
        chosen = await service._select_cheapest_bid_under_fuse(
            model_id="0xmodel",
            session_duration=1800,
        )

    assert chosen["bid_id"] == "0xbid_lo"
    # 0.0003 * 1800 * 338 = 182.52
    assert chosen["stake_mor"] == pytest.approx(182.52, rel=1e-3)


@pytest.mark.asyncio
async def test_cheapest_bid_skips_active_unhealthy_ghost(service):
    """Ghost cheapest bid not on active.mor.org healthy list must be ignored."""
    bids = {
        "bids": [
            {
                "Bid": {
                    "Id": "0xghost_cheap",
                    "Provider": "0xdeadhost",
                    "PricePerSecond": str(int(0.0001 * 1e18)),
                }
            },
            {
                "Bid": {
                    "Id": "0xhealthy",
                    "Provider": "0xgood",
                    "PricePerSecond": str(int(0.0003 * 1e18)),
                }
            },
        ]
    }
    resp = MagicMock()
    resp.json.return_value = bids
    p_policy, p_rated, p_healthy, p_settings = _patch_cheapest({"0xhealthy"}, resp)

    with p_policy as mock_policy, p_rated, p_healthy, p_settings as mock_settings:
        mock_policy.return_value.max_stake_mor = 700
        mock_settings.SESSION_STAKE_FACTOR = 338
        chosen = await service._select_cheapest_bid_under_fuse(
            model_id="0xmodel",
            session_duration=1800,
        )

    assert chosen["bid_id"] == "0xhealthy"


@pytest.mark.asyncio
async def test_cheapest_bid_over_fuse_refused(service):
    bids = {
        "bids": [
            {
                "Bid": {
                    "Id": "0xbid_hi",
                    "Provider": "0xprov",
                    "PricePerSecond": str(int(0.01 * 1e18)),
                }
            }
        ]
    }
    resp = MagicMock()
    resp.json.return_value = bids
    p_policy, p_rated, p_healthy, p_settings = _patch_cheapest({"0xbid_hi"}, resp)

    with p_policy as mock_policy, p_rated, p_healthy, p_settings as mock_settings:
        mock_policy.return_value.max_stake_mor = 700
        mock_settings.SESSION_STAKE_FACTOR = 338
        with pytest.raises(SessionStakeFuseError) as exc:
            await service._select_cheapest_bid_under_fuse(
                model_id="0xmodel",
                session_duration=1800,
            )

    # 0.01 * 1800 * 338 = 6084
    assert exc.value.stake_mor == pytest.approx(6084.0, rel=1e-3)
    assert exc.value.max_stake_mor == 700
    # 700 * 3600 / (1800 * 338) ≈ 4.14 MOR/hr (active.mor.org unit)
    assert "4.14 MOR/hr" in exc.value.message
    assert "active.mor.org" in exc.value.message
    assert "self-custody node" in exc.value.message


@pytest.mark.asyncio
async def test_cheapest_bid_no_bids(service):
    resp = MagicMock()
    resp.json.return_value = {"bids": []}
    p_policy, p_rated, p_healthy, _ = _patch_cheapest(set(), resp)
    with p_policy as mock_policy, p_rated, p_healthy:
        mock_policy.return_value.max_stake_mor = 700
        with pytest.raises(SessionOpenError):
            await service._select_cheapest_bid_under_fuse(
                model_id="0xmodel",
                session_duration=1800,
            )


@pytest.mark.asyncio
async def test_cheapest_bid_no_active_healthy_refuses(service):
    """Rated bids exist but none are active-healthy → do not open any."""
    bids = {
        "bids": [
            {
                "Bid": {
                    "Id": "0xghost",
                    "Provider": "0xdead",
                    "PricePerSecond": str(int(0.0001 * 1e18)),
                }
            }
        ]
    }
    resp = MagicMock()
    resp.json.return_value = bids
    p_policy, p_rated, p_healthy, p_settings = _patch_cheapest(set(), resp)
    with p_policy as mock_policy, p_rated, p_healthy, p_settings as mock_settings:
        mock_policy.return_value.max_stake_mor = 700
        mock_settings.SESSION_STAKE_FACTOR = 338
        with pytest.raises(SessionOpenError):
            await service._select_cheapest_bid_under_fuse(
                model_id="0xmodel",
                session_duration=1800,
            )
