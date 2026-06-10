"""Tests for SessionRoutingService.invalidate_session."""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.db.models import SessionState
from src.services.session_routing_service import SessionRoutingService


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def service():
    return SessionRoutingService()


def _ok_result():
    result = MagicMock()
    result.rowcount = 1
    return result


async def test_invalidate_marks_failed_and_schedules_close(service, mock_db):
    mock_db.execute.return_value = _ok_result()

    with patch(
        "src.services.session_routing_service.proxy_router_service.closeSession",
        new_callable=AsyncMock,
    ) as mock_close:
        ok = await service.invalidate_session(mock_db, "0xdead", "provider unreachable")
        # Let the fire-and-forget close task run
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert ok is True
    mock_db.execute.assert_awaited_once()
    mock_db.commit.assert_awaited_once()
    mock_close.assert_awaited_once_with("0xdead")
    update_stmt = mock_db.execute.await_args.args[0]
    compiled = str(update_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "FAILED" in compiled
    assert "state = 'OPEN'" in compiled


async def test_invalidate_with_expired_state_for_renewal(service, mock_db):
    mock_db.execute.return_value = _ok_result()

    with patch(
        "src.services.session_routing_service.proxy_router_service.closeSession",
        new_callable=AsyncMock,
    ):
        await service.invalidate_session(
            mock_db, "0xdead", "session expired on proxy", state=SessionState.EXPIRED
        )
        await asyncio.sleep(0)

    update_stmt = mock_db.execute.await_args.args[0]
    compiled = str(update_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "EXPIRED" in compiled


async def test_invalidate_noop_when_already_not_open(service, mock_db):
    result = MagicMock()
    result.rowcount = 0  # row was not OPEN (already FAILED/CLOSED/EXPIRED)
    mock_db.execute.return_value = result

    with patch(
        "src.services.session_routing_service.proxy_router_service.closeSession",
        new_callable=AsyncMock,
    ) as mock_close:
        ok = await service.invalidate_session(mock_db, "0xdead", "again")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert ok is False
    mock_close.assert_not_awaited()


async def test_invalidate_survives_close_failure(service, mock_db):
    mock_db.execute.return_value = _ok_result()

    with patch(
        "src.services.session_routing_service.proxy_router_service.closeSession",
        new_callable=AsyncMock,
        side_effect=Exception("proxy down"),
    ):
        # Must not raise even though the background close fails
        ok = await service.invalidate_session(mock_db, "0xdead", "reason")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert ok is True


async def test_invalidate_db_error_rolls_back_and_skips_close(service, mock_db):
    mock_db.execute.side_effect = Exception("db down")

    with patch(
        "src.services.session_routing_service.proxy_router_service.closeSession",
        new_callable=AsyncMock,
    ) as mock_close:
        ok = await service.invalidate_session(mock_db, "0xdead", "reason")
        await asyncio.sleep(0)

    assert ok is False
    mock_db.rollback.assert_awaited_once()
    mock_close.assert_not_awaited()
