"""Tests for premium showcase daylock budget (actual + open-stake holds)."""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services.session_routing_service import (
    SessionPremiumBudgetError,
    SessionRoutingService,
)


@pytest.fixture
def service():
    return SessionRoutingService()


async def test_non_premium_skips_budget(service):
    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_PREMIUM_MODEL_IDS = "0xprem"
        mock_settings.SESSION_PREMIUM_DAILY_BUDGET_MOR = 15000
        service._get_premium_daylock_spent = AsyncMock(
            side_effect=AssertionError("should not read budget for non-premium")
        )
        await service._ensure_premium_budget_allows_open("0xother")


async def test_budget_zero_skips(service):
    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_PREMIUM_MODEL_IDS = "0xprem"
        mock_settings.SESSION_PREMIUM_DAILY_BUDGET_MOR = 0
        service._get_premium_daylock_spent = AsyncMock(
            side_effect=AssertionError("budget 0 disables")
        )
        await service._ensure_premium_budget_allows_open("0xprem")


async def test_premium_allowed_under_budget(service):
    service._get_premium_daylock_spent = AsyncMock(return_value=1000.0)
    service._sum_premium_open_stake_mor = AsyncMock(return_value=200.0)

    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_PREMIUM_MODEL_IDS = "0xprem"
        mock_settings.SESSION_PREMIUM_DAILY_BUDGET_MOR = 15000
        await service._ensure_premium_budget_allows_open("0xprem")


async def test_premium_refused_when_actual_plus_holds_at_budget(service):
    service._get_premium_daylock_spent = AsyncMock(return_value=14000.0)
    service._sum_premium_open_stake_mor = AsyncMock(return_value=1000.0)

    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_PREMIUM_MODEL_IDS = "0xprem"
        mock_settings.SESSION_PREMIUM_DAILY_BUDGET_MOR = 15000
        with pytest.raises(SessionPremiumBudgetError) as exc_info:
            await service._ensure_premium_budget_allows_open("0xprem")

    err = exc_info.value
    assert err.category == "premium_budget"
    assert err.spent_mor == 14000.0
    assert err.holds_mor == 1000.0
    assert "exhausted" in err.message
    assert "nodedocs.mor.org" in err.message


async def test_premium_allowed_when_actual_high_but_holds_zero(service):
    """Early closes free holds; actual alone under budget still allows opens."""
    service._get_premium_daylock_spent = AsyncMock(return_value=14999.0)
    service._sum_premium_open_stake_mor = AsyncMock(return_value=0.0)

    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_PREMIUM_MODEL_IDS = "0xprem"
        mock_settings.SESSION_PREMIUM_DAILY_BUDGET_MOR = 15000
        await service._ensure_premium_budget_allows_open("0xprem")


def test_daylock_prorata_early_close(service):
    # stake 100, lived half of schedule → 50
    daylock = service._daylock_prorata_mor(
        stake_mor=100.0,
        opened_at=1_000_000,
        ends_at=1_000_000 + 1200,
        closed_at=1_000_000 + 600,
    )
    assert daylock == pytest.approx(50.0)


def test_daylock_prorata_full_duration(service):
    daylock = service._daylock_prorata_mor(
        stake_mor=100.0,
        opened_at=1_000_000,
        ends_at=1_000_000 + 1200,
        closed_at=1_000_000 + 1200,
    )
    assert daylock == pytest.approx(100.0)


def test_daylock_prorata_late_close_zero(service):
    daylock = service._daylock_prorata_mor(
        stake_mor=100.0,
        opened_at=1_000_000,
        ends_at=1_000_000 + 1200,
        closed_at=1_000_000 + 1201,
    )
    assert daylock == 0.0


async def test_record_daylock_premium_prorata_meters_redis(service):
    now = int(datetime.now(timezone.utc).timestamp())
    session = MagicMock()
    session.id = "0xsid"
    session.model_id = "0xprem"
    session.stake_mor = 100.0
    session.daylock_mor = None
    session.created_at = datetime.fromtimestamp(now - 600, tz=timezone.utc).replace(
        tzinfo=None
    )
    session.expires_at = datetime.fromtimestamp(now + 600, tz=timezone.utc).replace(
        tzinfo=None
    )

    status = {
        "session": {
            "Stake": str(int(100 * 1e18)),
            "OpenedAt": now - 600,
            "EndsAt": now + 600,
            "ClosedAt": now,
        }
    }
    service._add_premium_daylock_spent = AsyncMock()
    service._daylock_from_close_receipt = AsyncMock(return_value=None)

    with patch(
        "src.services.session_routing_service.proxy_router_service.getSessionStatus",
        new=AsyncMock(return_value=status),
    ), patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_PREMIUM_MODEL_IDS = "0xprem"
        mock_settings.SESSION_PREMIUM_DAILY_BUDGET_MOR = 15000
        mock_settings.MOR_TOKEN_ADDRESS = None
        mock_settings.SESSION_CONSUMER_WALLET_ADDRESS = None
        mock_settings.WEB3_PROVIDER_URL = None
        await service._record_daylock_on_close(session, close_result={"tx": "0x" + "ab" * 32})

    # lived 600 / sched 1200 * 100 = 50
    assert float(session.daylock_mor) == pytest.approx(50.0)
    service._add_premium_daylock_spent.assert_awaited_once()
    assert service._add_premium_daylock_spent.await_args.args[0] == pytest.approx(50.0)


async def test_record_daylock_warm_persists_but_skips_redis(service):
    now = int(datetime.now(timezone.utc).timestamp())
    session = MagicMock()
    session.id = "0xwarm"
    session.model_id = "0xwarm"
    session.stake_mor = 40.0
    session.daylock_mor = None
    session.created_at = None
    session.expires_at = None

    status = {
        "session": {
            "Stake": str(int(40 * 1e18)),
            "OpenedAt": now - 300,
            "EndsAt": now + 900,
            "ClosedAt": now,
        }
    }
    service._add_premium_daylock_spent = AsyncMock()

    with patch(
        "src.services.session_routing_service.proxy_router_service.getSessionStatus",
        new=AsyncMock(return_value=status),
    ), patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_PREMIUM_MODEL_IDS = "0xprem"
        mock_settings.SESSION_PREMIUM_DAILY_BUDGET_MOR = 15000
        mock_settings.MOR_TOKEN_ADDRESS = None
        mock_settings.SESSION_CONSUMER_WALLET_ADDRESS = None
        mock_settings.WEB3_PROVIDER_URL = None
        await service._record_daylock_on_close(session)

    assert float(session.daylock_mor) == pytest.approx(10.0)  # 300/1200 * 40
    service._add_premium_daylock_spent.assert_not_awaited()


async def test_record_daylock_receipt_preferred(service):
    now = int(datetime.now(timezone.utc).timestamp())
    session = MagicMock()
    session.id = "0xsid"
    session.model_id = "0xprem"
    session.stake_mor = 100.0
    session.daylock_mor = None
    session.created_at = None
    session.expires_at = None

    status = {
        "session": {
            "Stake": str(int(100 * 1e18)),
            "OpenedAt": now - 600,
            "EndsAt": now + 600,
            "ClosedAt": now,
        }
    }
    service._add_premium_daylock_spent = AsyncMock()
    service._daylock_from_close_receipt = AsyncMock(return_value=33.5)

    with patch(
        "src.services.session_routing_service.proxy_router_service.getSessionStatus",
        new=AsyncMock(return_value=status),
    ), patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_PREMIUM_MODEL_IDS = "0xprem"
        mock_settings.SESSION_PREMIUM_DAILY_BUDGET_MOR = 15000
        await service._record_daylock_on_close(
            session, close_result={"tx": "0x" + "cd" * 32}
        )

    assert float(session.daylock_mor) == pytest.approx(33.5)
    service._add_premium_daylock_spent.assert_awaited_once_with(33.5)
