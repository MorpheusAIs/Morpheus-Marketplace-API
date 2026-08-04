"""Tests for warm-model MOR low-water mark (liquid wallet reserve)."""
import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services.session_routing_service import (
    SessionMorReservedError,
    SessionRoutingService,
)


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


@pytest.fixture
def service():
    return SessionRoutingService()


def _patch_get_db(db):
    @asynccontextmanager
    async def _fake_get_db():
        yield db
    return patch("src.services.session_routing_service.get_db", _fake_get_db)


def _balance_response(mor_wei):
    resp = MagicMock()
    resp.json.return_value = {"mor": str(mor_wei), "eth": "0"}
    return resp


def test_parse_mor_balance_wei():
    assert SessionRoutingService._parse_mor_balance_wei("30000000000000000000000") == pytest.approx(30000.0)
    assert SessionRoutingService._parse_mor_balance_wei(10**18) == pytest.approx(1.0)
    assert SessionRoutingService._parse_mor_balance_wei(None) is None
    assert SessionRoutingService._parse_mor_balance_wei("not-a-number") is None


async def test_watermark_zero_skips_check(service):
    with patch(
        "src.services.session_routing_service.settings"
    ) as mock_settings:
        mock_settings.SESSION_MOR_LOW_WATER_MARK_MOR = 0
        mock_settings.SESSION_PREFERRED_MODELS = ""
        service._get_liquid_mor_balance = AsyncMock(
            side_effect=AssertionError("should not read balance when disabled")
        )
        await service._ensure_mor_low_water_allows_open("0xcold")


async def test_warm_model_allowed_below_watermark(service):
    with patch(
        "src.services.session_routing_service.settings"
    ) as mock_settings:
        mock_settings.SESSION_MOR_LOW_WATER_MARK_MOR = 30000
        mock_settings.SESSION_PREFERRED_MODELS = "0xwarm"
        service._get_liquid_mor_balance = AsyncMock(
            side_effect=AssertionError("warm models skip balance read")
        )
        await service._ensure_mor_low_water_allows_open("0xwarm")


async def test_non_warm_refused_at_or_below_watermark(service):
    service._get_liquid_mor_balance = AsyncMock(return_value=25000.0)

    with patch(
        "src.services.session_routing_service.settings"
    ) as mock_settings:
        mock_settings.SESSION_MOR_LOW_WATER_MARK_MOR = 30000
        mock_settings.SESSION_PREFERRED_MODELS = "0xwarm"
        with pytest.raises(SessionMorReservedError) as exc_info:
            await service._ensure_mor_low_water_allows_open("0xcold")

    err = exc_info.value
    assert err.model_id == "0xcold"
    assert err.balance_mor == 25000.0
    assert err.low_water_mark_mor == 30000
    assert "priority" in err.message.lower()
    assert "nodedocs.mor.org" in err.message


async def test_non_warm_allowed_above_watermark(service):
    service._get_liquid_mor_balance = AsyncMock(return_value=35000.0)

    with patch(
        "src.services.session_routing_service.settings"
    ) as mock_settings:
        mock_settings.SESSION_MOR_LOW_WATER_MARK_MOR = 30000
        mock_settings.SESSION_PREFERRED_MODELS = "0xwarm"
        await service._ensure_mor_low_water_allows_open("0xcold")


async def test_balance_fetch_failure_fail_open(service):
    service._get_liquid_mor_balance = AsyncMock(return_value=None)

    with patch(
        "src.services.session_routing_service.settings"
    ) as mock_settings:
        mock_settings.SESSION_MOR_LOW_WATER_MARK_MOR = 30000
        mock_settings.SESSION_PREFERRED_MODELS = ""
        await service._ensure_mor_low_water_allows_open("0xcold")


async def test_get_liquid_mor_balance_parses_and_caches(service):
    with patch(
        "src.services.session_routing_service.proxy_router_service.getBlockchainBalance",
        new_callable=AsyncMock,
        return_value=_balance_response(30_000 * 10**18),
    ) as mock_bal, patch(
        "src.services.session_routing_service.settings"
    ) as mock_settings:
        mock_settings.SESSION_MOR_BALANCE_CACHE_SECONDS = 15
        first = await service._get_liquid_mor_balance()
        second = await service._get_liquid_mor_balance()

    assert first == pytest.approx(30000.0)
    assert second == pytest.approx(30000.0)
    mock_bal.assert_awaited_once()


async def test_route_request_idle_claim_skips_low_water(service, mock_db):
    """Idle claims must succeed even when below the watermark."""
    service._claim_idle_session = AsyncMock(return_value="0xidle")
    service._open_session_for_model = AsyncMock()
    service._ensure_mor_low_water_allows_open = AsyncMock(
        side_effect=AssertionError("should not low-water-check on idle claim")
    )
    service._ensure_soft_cap_allows_open = AsyncMock(
        side_effect=AssertionError("should not soft-cap-check on idle claim")
    )

    with _patch_get_db(mock_db), patch(
        "src.services.session_routing_service.model_router.get_target_model",
        new_callable=AsyncMock,
        return_value="0xcold",
    ):
        session_id = await service.route_request(user_id=1, requested_model="m")

    assert session_id == "0xidle"
    service._open_session_for_model.assert_not_awaited()


async def test_route_request_refuses_non_warm_open_at_watermark(service, mock_db):
    service._claim_idle_session = AsyncMock(return_value=None)
    service._ensure_soft_cap_allows_open = AsyncMock()
    service._ensure_mor_low_water_allows_open = AsyncMock(
        side_effect=SessionMorReservedError(
            "reserved",
            model_id="0xcold",
            balance_mor=20000.0,
            low_water_mark_mor=30000,
        )
    )
    service._open_session_for_model = AsyncMock()

    with _patch_get_db(mock_db), patch(
        "src.services.session_routing_service.model_router.get_target_model",
        new_callable=AsyncMock,
        return_value="0xcold",
    ):
        with pytest.raises(SessionMorReservedError):
            await service.route_request(user_id=1, requested_model="m")

    service._open_session_for_model.assert_not_awaited()
