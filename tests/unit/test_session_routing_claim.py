"""Tests for SessionRoutingService.route_request atomic-claim routing.

These cover the lock-free fast path (atomic ``SKIP LOCKED`` claim) and the
serialized open fallback introduced to replace the old
"take per-model lock -> SELECT all rows -> pick in Python -> separate UPDATE"
flow. The DB is mocked (the project has no Postgres test fixture), so these
validate the service control flow and the statement the claim issues; the
``FOR UPDATE SKIP LOCKED`` semantics themselves are a Postgres idiom exercised
at runtime/CI against the real database.
"""
import os
import sys
from contextlib import asynccontextmanager
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
    db.rollback = AsyncMock()
    return db


@pytest.fixture
def service():
    return SessionRoutingService()


def _claim_result(value):
    """A db.execute() result whose scalar_one_or_none() yields ``value``."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


# ---------------------------------------------------------------------------
# route_request control flow
#
# route_request no longer takes a `db` param — it manages its own short-lived
# connections (so it holds none while waiting on the wallet lock). It opens at
# most one claim attempt, then opens a new session created already-assigned
# (no separate _assign step, no re-claim).
# ---------------------------------------------------------------------------


def _patch_get_db(db):
    @asynccontextmanager
    async def _fake_get_db():
        yield db
    return patch("src.services.session_routing_service.get_db", _fake_get_db)


async def test_fast_path_returns_claimed_session_without_open(service, mock_db):
    """An idle session is claimed lock-free; no open."""
    service._claim_idle_session = AsyncMock(return_value="0xidle")
    service._open_session_for_model = AsyncMock()

    with _patch_get_db(mock_db), patch(
        "src.services.session_routing_service.model_router.get_target_model",
        new_callable=AsyncMock,
        return_value="0xmodel",
    ):
        session_id = await service.route_request(user_id=1, requested_model="m")

    assert session_id == "0xidle"
    service._claim_idle_session.assert_awaited_once_with(mock_db, "0xmodel", omit_provider=None)
    service._open_session_for_model.assert_not_awaited()


async def test_open_path_opens_assigned_session_when_none_idle(service, mock_db):
    """Fast-path miss -> open a new session, created already assigned (active_requests=1)."""
    service._claim_idle_session = AsyncMock(return_value=None)
    service._open_session_for_model = AsyncMock(return_value="0xnew")

    with _patch_get_db(mock_db), patch(
        "src.services.session_routing_service.model_router.get_target_model",
        new_callable=AsyncMock,
        return_value="0xmodel",
    ):
        session_id = await service.route_request(user_id=1, requested_model="m")

    assert session_id == "0xnew"
    service._claim_idle_session.assert_awaited_once()
    service._open_session_for_model.assert_awaited_once()
    _, kwargs = service._open_session_for_model.call_args
    assert kwargs.get("initial_active_requests") == 1


# ---------------------------------------------------------------------------
# _claim_idle_session statement / transaction contract
# ---------------------------------------------------------------------------


async def test_claim_issues_skip_locked_update_and_commits(service, mock_db):
    mock_db.execute.return_value = _claim_result("0xidle")

    claimed = await service._claim_idle_session(mock_db, "0xmodel")

    assert claimed == "0xidle"
    mock_db.execute.assert_awaited_once()
    mock_db.commit.assert_awaited_once()

    stmt, params = mock_db.execute.await_args.args
    sql = str(stmt)
    assert "UPDATE routed_sessions" in sql
    assert "active_requests = active_requests + 1" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "active_requests = 0" in sql
    assert "NULLS FIRST" in sql
    assert "RETURNING id" in sql
    assert params["model_id"] == "0xmodel"
    assert params["open_state"] == SessionState.OPEN.value
    assert "now" in params


async def test_claim_returns_none_when_no_idle_row(service, mock_db):
    mock_db.execute.return_value = _claim_result(None)

    claimed = await service._claim_idle_session(mock_db, "0xmodel")

    assert claimed is None
    mock_db.commit.assert_awaited_once()


async def test_claim_rolls_back_and_reraises_on_db_error(service, mock_db):
    mock_db.execute.side_effect = RuntimeError("db down")

    with pytest.raises(RuntimeError):
        await service._claim_idle_session(mock_db, "0xmodel")

    mock_db.rollback.assert_awaited_once()
    mock_db.commit.assert_not_awaited()
