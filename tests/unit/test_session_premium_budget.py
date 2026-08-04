"""Tests for premium showcase daily daylock budget (metered on close)."""
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

    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_PREMIUM_MODEL_IDS = "0xprem"
        mock_settings.SESSION_PREMIUM_DAILY_BUDGET_MOR = 15000
        await service._ensure_premium_budget_allows_open("0xprem")


async def test_premium_refused_at_budget(service):
    service._get_premium_daylock_spent = AsyncMock(return_value=15000.0)

    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_PREMIUM_MODEL_IDS = "0xprem"
        mock_settings.SESSION_PREMIUM_DAILY_BUDGET_MOR = 15000
        with pytest.raises(SessionPremiumBudgetError) as exc_info:
            await service._ensure_premium_budget_allows_open("0xprem")

    err = exc_info.value
    assert err.category == "premium_budget"
    assert err.spent_mor == 15000.0
    assert "exhausted" in err.message
    assert "nodedocs.mor.org" in err.message


async def test_record_premium_daylock_on_close(service):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session = MagicMock()
    session.id = "0xsid"
    session.model_id = "0xprem"
    session.created_at = now - timedelta(seconds=600)
    session.expires_at = now + timedelta(seconds=1200)

    service._get_model_max_price_per_second = AsyncMock(return_value=0.0005)
    service._add_premium_daylock_spent = AsyncMock()

    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_PREMIUM_MODEL_IDS = "0xprem"
        mock_settings.SESSION_PREMIUM_DAILY_BUDGET_MOR = 15000
        mock_settings.SESSION_STAKE_FACTOR = 338
        await service._record_premium_daylock_on_close(session)

    # 0.0005 * 600 * 338 = 101.4
    service._add_premium_daylock_spent.assert_awaited_once()
    amount = service._add_premium_daylock_spent.await_args.args[0]
    assert amount == pytest.approx(101.4, rel=1e-3)


async def test_record_skips_non_premium(service):
    session = MagicMock()
    session.model_id = "0xother"
    service._add_premium_daylock_spent = AsyncMock()

    with patch("src.services.session_routing_service.settings") as mock_settings:
        mock_settings.SESSION_PREMIUM_MODEL_IDS = "0xprem"
        mock_settings.SESSION_PREMIUM_DAILY_BUDGET_MOR = 15000
        await service._record_premium_daylock_on_close(session)

    service._add_premium_daylock_spent.assert_not_awaited()
