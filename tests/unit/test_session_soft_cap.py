"""Tests for per-model OPEN session soft caps."""
import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services.session_routing_service import (
    SessionPoolBusyError,
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


def _count_result(n: int):
    result = MagicMock()
    result.scalar_one.return_value = n
    return result


def test_soft_cap_warm_vs_default(service):
    with patch(
        "src.services.session_routing_service.settings"
    ) as mock_settings:
        mock_settings.SESSION_PREFERRED_MODELS = "0xwarm,0xotherwarm"
        mock_settings.SESSION_SOFT_CAP_WARM = 12
        mock_settings.SESSION_SOFT_CAP_DEFAULT = 4
        assert service._soft_cap_for_model("0xwarm") == 12
        assert service._soft_cap_for_model("0xcold") == 4


def test_soft_cap_zero_means_unlimited(service):
    with patch(
        "src.services.session_routing_service.settings"
    ) as mock_settings:
        mock_settings.SESSION_PREFERRED_MODELS = ""
        mock_settings.SESSION_SOFT_CAP_WARM = 0
        mock_settings.SESSION_SOFT_CAP_DEFAULT = 0
        assert service._soft_cap_for_model("0xany") == 0


async def test_ensure_soft_cap_allows_when_disabled(service, mock_db):
    with _patch_get_db(mock_db), patch(
        "src.services.session_routing_service.settings"
    ) as mock_settings:
        mock_settings.SESSION_PREFERRED_MODELS = ""
        mock_settings.SESSION_SOFT_CAP_DEFAULT = 0
        mock_settings.SESSION_SOFT_CAP_WARM = 0
        await service._ensure_soft_cap_allows_open("0xmodel")
    mock_db.execute.assert_not_awaited()


async def test_ensure_soft_cap_raises_when_at_capacity(service, mock_db):
    mock_db.execute.return_value = _count_result(4)

    with _patch_get_db(mock_db), patch(
        "src.services.session_routing_service.settings"
    ) as mock_settings:
        mock_settings.SESSION_PREFERRED_MODELS = ""
        mock_settings.SESSION_SOFT_CAP_DEFAULT = 4
        mock_settings.SESSION_SOFT_CAP_WARM = 12
        mock_settings.SESSION_SOFT_CAP_RETRY_AFTER_SECONDS = 15
        with pytest.raises(SessionPoolBusyError) as exc_info:
            await service._ensure_soft_cap_allows_open("0xmodel")

    err = exc_info.value
    assert err.soft_cap == 4
    assert err.open_count == 4
    assert err.retry_after == 15
    assert err.model_id == "0xmodel"


async def test_ensure_soft_cap_allows_when_under_cap(service, mock_db):
    mock_db.execute.return_value = _count_result(3)

    with _patch_get_db(mock_db), patch(
        "src.services.session_routing_service.settings"
    ) as mock_settings:
        mock_settings.SESSION_PREFERRED_MODELS = ""
        mock_settings.SESSION_SOFT_CAP_DEFAULT = 4
        mock_settings.SESSION_SOFT_CAP_WARM = 12
        mock_settings.SESSION_SOFT_CAP_RETRY_AFTER_SECONDS = 15
        await service._ensure_soft_cap_allows_open("0xmodel")


async def test_route_request_busy_when_no_idle_and_at_cap(service, mock_db):
    service._claim_idle_session = AsyncMock(return_value=None)
    service._open_session_for_model = AsyncMock()
    service._ensure_soft_cap_allows_open = AsyncMock(
        side_effect=SessionPoolBusyError(
            "busy",
            model_id="0xmodel",
            retry_after=15,
            soft_cap=4,
            open_count=4,
        )
    )

    with _patch_get_db(mock_db), patch(
        "src.services.session_routing_service.model_router.get_target_model",
        new_callable=AsyncMock,
        return_value="0xmodel",
    ):
        with pytest.raises(SessionPoolBusyError):
            await service.route_request(user_id=1, requested_model="m")

    service._open_session_for_model.assert_not_awaited()


async def test_route_request_still_claims_idle_at_cap(service, mock_db):
    """Soft cap only blocks opens; idle claims still succeed."""
    service._claim_idle_session = AsyncMock(return_value="0xidle")
    service._open_session_for_model = AsyncMock()
    service._ensure_soft_cap_allows_open = AsyncMock(
        side_effect=AssertionError("should not soft-cap-check on idle claim")
    )

    with _patch_get_db(mock_db), patch(
        "src.services.session_routing_service.model_router.get_target_model",
        new_callable=AsyncMock,
        return_value="0xmodel",
    ):
        session_id = await service.route_request(user_id=1, requested_model="m")

    assert session_id == "0xidle"
    service._open_session_for_model.assert_not_awaited()
