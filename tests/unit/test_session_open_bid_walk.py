"""Open-time bid walk: capacity failover without A↔B ping-pong."""

import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.core.session_routing_policy import reset_session_routing_policy_cache
from src.services import proxy_router_service
from src.services.session_routing_service import SessionOpenError, SessionRoutingService


@pytest.fixture
def service():
    reset_session_routing_policy_cache()
    svc = SessionRoutingService()
    svc._provider_open_cooldown.clear()
    return svc


def _rated_two_providers():
    bids = {
        "bids": [
            {
                "Bid": {
                    "Id": "0xbid_a",
                    "Provider": "0xprov_a",
                    "PricePerSecond": str(int(0.0002 * 1e18)),
                }
            },
            {
                "Bid": {
                    "Id": "0xbid_b",
                    "Provider": "0xprov_b",
                    "PricePerSecond": str(int(0.0003 * 1e18)),
                }
            },
            {
                "Bid": {
                    "Id": "0xbid_c",
                    "Provider": "0xprov_c",
                    "PricePerSecond": str(int(0.0004 * 1e18)),
                }
            },
        ]
    }
    resp = MagicMock()
    resp.json.return_value = bids
    return resp


def _capacity_error(msg="no capacity"):
    return proxy_router_service.ProxyRouterServiceError(
        f"Failed to open bid session: {msg}",
        status_code=500,
        error_type="unknown",
    )


@pytest.mark.asyncio
async def test_open_walk_retries_next_provider_on_no_capacity(service):
    """Cheapest no_capacity → open next peer; never revisit omitted provider."""
    resp = _rated_two_providers()
    open_calls = []

    async def fake_open(*, bid_id, session_duration):
        open_calls.append(bid_id)
        if bid_id == "0xbid_a":
            raise _capacity_error("no capacity")
        return {"sessionID": "0xsession_b"}

    with (
        patch(
            "src.services.session_routing_service.get_session_routing_policy"
        ) as mock_policy,
        patch(
            "src.services.session_routing_service.proxy_router_service.getRatedBids",
            new=AsyncMock(return_value=resp),
        ),
        patch(
            "src.services.session_routing_service.direct_model_service.get_healthy_bid_ids_for_model",
            new=AsyncMock(
                return_value={"0xbid_a", "0xbid_b", "0xbid_c"}
            ),
        ),
        patch(
            "src.services.session_routing_service.proxy_router_service.openSessionByBid",
            new=fake_open,
        ),
        patch.object(
            service, "_ensure_allowlist_allows_open", new=AsyncMock()
        ),
        patch.object(service, "_ensure_soft_cap_allows_open", new=AsyncMock()),
        patch.object(
            service, "_ensure_mor_low_water_allows_open", new=AsyncMock()
        ),
        patch.object(
            service, "_ensure_max_bid_pps_allows_open", new=AsyncMock()
        ),
        patch.object(
            service, "_ensure_premium_budget_allows_open", new=AsyncMock()
        ),
        patch.object(
            service, "_is_expensive_model", new=AsyncMock(return_value=False)
        ),
        patch.object(
            service,
            "_fetch_session_status",
            new=AsyncMock(return_value={"provider": "0xprov_b", "stake": "0"}),
        ),
        patch.object(
            service,
            "_resolve_expires_at",
            return_value=time.time(),
        ),
        patch.object(
            service, "_parse_provider_address", return_value="0xprov_b"
        ),
        patch.object(service, "_parse_session_stake_mor", return_value=1.0),
        patch(
            "src.services.session_routing_service.get_db"
        ) as mock_get_db,
        patch("src.services.session_routing_service.settings") as mock_settings,
    ):
        mock_policy.return_value.max_stake_mor = 700
        mock_settings.SESSION_STAKE_FACTOR = 338
        mock_settings.SESSION_DEFAULT_DURATION_SECONDS = 1800
        mock_settings.SESSION_EXPENSIVE_DEFAULT_DURATION_SECONDS = 1200
        mock_settings.SESSION_OPEN_BID_WALK_MAX_ATTEMPTS = 3
        mock_settings.SESSION_OPEN_PROVIDER_COOLDOWN_SECONDS = 120
        mock_settings.SESSION_ONCHAIN_THROTTLE_COOLDOWN_SECONDS = 0

        db = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=None)

        session_id = await service._open_session_for_model(
            model_id="0xmodel",
            model_name="test",
            model_type="LLM",
            initial_active_requests=1,
        )

    assert session_id == "0xsession_b"
    assert open_calls == ["0xbid_a", "0xbid_b"]
    assert service._provider_on_open_cooldown("0xmodel", "0xprov_a")
    assert not service._provider_on_open_cooldown("0xmodel", "0xprov_b")


@pytest.mark.asyncio
async def test_open_walk_no_ping_pong_accumulates_omits(service):
    """A and B both capacity → try C once; A/B never re-tried in same walk."""
    resp = _rated_two_providers()
    open_calls = []

    async def fake_open(*, bid_id, session_duration):
        open_calls.append(bid_id)
        raise _capacity_error("no capacity")

    with (
        patch(
            "src.services.session_routing_service.get_session_routing_policy"
        ) as mock_policy,
        patch(
            "src.services.session_routing_service.proxy_router_service.getRatedBids",
            new=AsyncMock(return_value=resp),
        ),
        patch(
            "src.services.session_routing_service.direct_model_service.get_healthy_bid_ids_for_model",
            new=AsyncMock(
                return_value={"0xbid_a", "0xbid_b", "0xbid_c"}
            ),
        ),
        patch(
            "src.services.session_routing_service.proxy_router_service.openSessionByBid",
            new=fake_open,
        ),
        patch.object(
            service, "_ensure_allowlist_allows_open", new=AsyncMock()
        ),
        patch.object(service, "_ensure_soft_cap_allows_open", new=AsyncMock()),
        patch.object(
            service, "_ensure_mor_low_water_allows_open", new=AsyncMock()
        ),
        patch.object(
            service, "_ensure_max_bid_pps_allows_open", new=AsyncMock()
        ),
        patch.object(
            service, "_ensure_premium_budget_allows_open", new=AsyncMock()
        ),
        patch.object(
            service, "_is_expensive_model", new=AsyncMock(return_value=False)
        ),
        patch("src.services.session_routing_service.settings") as mock_settings,
    ):
        mock_policy.return_value.max_stake_mor = 700
        mock_settings.SESSION_STAKE_FACTOR = 338
        mock_settings.SESSION_DEFAULT_DURATION_SECONDS = 1800
        mock_settings.SESSION_EXPENSIVE_DEFAULT_DURATION_SECONDS = 1200
        mock_settings.SESSION_OPEN_BID_WALK_MAX_ATTEMPTS = 3
        mock_settings.SESSION_OPEN_PROVIDER_COOLDOWN_SECONDS = 120
        mock_settings.SESSION_ONCHAIN_THROTTLE_COOLDOWN_SECONDS = 0

        with pytest.raises(SessionOpenError):
            await service._open_session_for_model(
                model_id="0xmodel",
                model_name="test",
            )

    assert open_calls == ["0xbid_a", "0xbid_b", "0xbid_c"]
    assert service._provider_on_open_cooldown("0xmodel", "0xprov_a")
    assert service._provider_on_open_cooldown("0xmodel", "0xprov_b")
    assert service._provider_on_open_cooldown("0xmodel", "0xprov_c")


@pytest.mark.asyncio
async def test_open_walk_respects_cooldown_across_opens(service):
    """Second open skips cooled provider and starts at next peer."""
    resp = _rated_two_providers()
    service._mark_provider_open_cooldown("0xmodel", "0xprov_a")
    # Force a long cool-off for the assertion window.
    key = service._provider_cooldown_key("0xmodel", "0xprov_a")
    service._provider_open_cooldown[key] = time.monotonic() + 120

    open_calls = []

    async def fake_open(*, bid_id, session_duration):
        open_calls.append(bid_id)
        return {"sessionID": "0xsession_b"}

    with (
        patch(
            "src.services.session_routing_service.get_session_routing_policy"
        ) as mock_policy,
        patch(
            "src.services.session_routing_service.proxy_router_service.getRatedBids",
            new=AsyncMock(return_value=resp),
        ),
        patch(
            "src.services.session_routing_service.direct_model_service.get_healthy_bid_ids_for_model",
            new=AsyncMock(
                return_value={"0xbid_a", "0xbid_b", "0xbid_c"}
            ),
        ),
        patch(
            "src.services.session_routing_service.proxy_router_service.openSessionByBid",
            new=fake_open,
        ),
        patch.object(
            service, "_ensure_allowlist_allows_open", new=AsyncMock()
        ),
        patch.object(service, "_ensure_soft_cap_allows_open", new=AsyncMock()),
        patch.object(
            service, "_ensure_mor_low_water_allows_open", new=AsyncMock()
        ),
        patch.object(
            service, "_ensure_max_bid_pps_allows_open", new=AsyncMock()
        ),
        patch.object(
            service, "_ensure_premium_budget_allows_open", new=AsyncMock()
        ),
        patch.object(
            service, "_is_expensive_model", new=AsyncMock(return_value=False)
        ),
        patch.object(
            service,
            "_fetch_session_status",
            new=AsyncMock(return_value={}),
        ),
        patch.object(service, "_resolve_expires_at", return_value=time.time()),
        patch.object(service, "_parse_provider_address", return_value="0xprov_b"),
        patch.object(service, "_parse_session_stake_mor", return_value=1.0),
        patch("src.services.session_routing_service.get_db") as mock_get_db,
        patch("src.services.session_routing_service.settings") as mock_settings,
    ):
        mock_policy.return_value.max_stake_mor = 700
        mock_settings.SESSION_STAKE_FACTOR = 338
        mock_settings.SESSION_DEFAULT_DURATION_SECONDS = 1800
        mock_settings.SESSION_EXPENSIVE_DEFAULT_DURATION_SECONDS = 1200
        mock_settings.SESSION_OPEN_BID_WALK_MAX_ATTEMPTS = 3
        mock_settings.SESSION_OPEN_PROVIDER_COOLDOWN_SECONDS = 120
        mock_settings.SESSION_ONCHAIN_THROTTLE_COOLDOWN_SECONDS = 0

        db = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=None)

        await service._open_session_for_model(model_id="0xmodel")

    assert open_calls == ["0xbid_b"]


@pytest.mark.asyncio
async def test_open_walk_non_retryable_does_not_advance(service):
    resp = _rated_two_providers()
    open_calls = []

    async def fake_open(*, bid_id, session_duration):
        open_calls.append(bid_id)
        raise proxy_router_service.ProxyRouterServiceError(
            "Failed to open bid session: insufficient funds for gas",
            status_code=500,
            error_type="unknown",
        )

    with (
        patch(
            "src.services.session_routing_service.get_session_routing_policy"
        ) as mock_policy,
        patch(
            "src.services.session_routing_service.proxy_router_service.getRatedBids",
            new=AsyncMock(return_value=resp),
        ),
        patch(
            "src.services.session_routing_service.direct_model_service.get_healthy_bid_ids_for_model",
            new=AsyncMock(
                return_value={"0xbid_a", "0xbid_b", "0xbid_c"}
            ),
        ),
        patch(
            "src.services.session_routing_service.proxy_router_service.openSessionByBid",
            new=fake_open,
        ),
        patch.object(
            service, "_ensure_allowlist_allows_open", new=AsyncMock()
        ),
        patch.object(service, "_ensure_soft_cap_allows_open", new=AsyncMock()),
        patch.object(
            service, "_ensure_mor_low_water_allows_open", new=AsyncMock()
        ),
        patch.object(
            service, "_ensure_max_bid_pps_allows_open", new=AsyncMock()
        ),
        patch.object(
            service, "_ensure_premium_budget_allows_open", new=AsyncMock()
        ),
        patch.object(
            service, "_is_expensive_model", new=AsyncMock(return_value=False)
        ),
        patch("src.services.session_routing_service.settings") as mock_settings,
    ):
        mock_policy.return_value.max_stake_mor = 700
        mock_settings.SESSION_STAKE_FACTOR = 338
        mock_settings.SESSION_DEFAULT_DURATION_SECONDS = 1800
        mock_settings.SESSION_EXPENSIVE_DEFAULT_DURATION_SECONDS = 1200
        mock_settings.SESSION_OPEN_BID_WALK_MAX_ATTEMPTS = 3
        mock_settings.SESSION_OPEN_PROVIDER_COOLDOWN_SECONDS = 120
        mock_settings.SESSION_ONCHAIN_THROTTLE_COOLDOWN_SECONDS = 0

        with pytest.raises(SessionOpenError, match="insufficient funds"):
            await service._open_session_for_model(model_id="0xmodel")

    assert open_calls == ["0xbid_a"]


def test_is_open_bid_walk_retryable_patterns():
    assert SessionRoutingService._is_open_bid_walk_retryable(
        _capacity_error("no capacity")
    )
    assert SessionRoutingService._is_open_bid_walk_retryable(
        proxy_router_service.ProxyRouterServiceError(
            "connect failed", error_type="network_error"
        )
    )
    assert not SessionRoutingService._is_open_bid_walk_retryable(
        proxy_router_service.ProxyRouterServiceError(
            "nonce too low", error_type="unknown"
        )
    )
