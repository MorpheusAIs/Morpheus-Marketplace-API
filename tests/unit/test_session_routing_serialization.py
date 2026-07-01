"""Tests for the ADAPTIVE on-chain wallet throttle in SessionRoutingService.

The single consumer wallet has no nonce lock in the C-Node, so a simultaneous
BURST of txs (a failover storm) collides on nonce, while normal time-staggered
load sequences fine. So on-chain ops run CONCURRENTLY on the happy path and
serialize on the wallet lock only after a nonce conflict is observed, for a
short cooldown window. New request-path sessions are still created already
assigned (active_requests=1), and no DB connection is held across the on-chain
call. These tests pin that behavior.
"""
import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.db.models import SessionState
from src.services import proxy_router_service
from src.services.proxy_router_service import ProxyRouterServiceError
from src.services.session_routing_service import SessionRoutingService


@pytest.fixture
def service():
    return SessionRoutingService()


def _make_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _patch_get_db(db):
    @asynccontextmanager
    async def _fake_get_db():
        yield db
    return patch("src.services.session_routing_service.get_db", _fake_get_db)


def _overlap_tracker(state, return_value):
    """Async stand-in for an on-chain call that records peak concurrency."""

    async def _call(*args, **kwargs):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.01)
        state["cur"] -= 1
        return return_value

    return _call


# ---------------------------------------------------------------------------
# is_nonce_error detector
# ---------------------------------------------------------------------------


def test_is_nonce_error_matches_signatures():
    assert proxy_router_service.is_nonce_error("HTTP 500: replacement transaction underpriced")
    assert proxy_router_service.is_nonce_error("nonce too low")
    assert proxy_router_service.is_nonce_error("Nonce error for 0xabc")
    assert not proxy_router_service.is_nonce_error("no provider accepting session")
    assert not proxy_router_service.is_nonce_error("insufficient balance")
    assert not proxy_router_service.is_nonce_error("")


# ---------------------------------------------------------------------------
# Happy path: NOT serialized (the whole point of adaptive throttling).
# ---------------------------------------------------------------------------


async def test_normal_mode_opens_run_concurrently(service):
    state = {"cur": 0, "max": 0}
    with _patch_get_db(_make_db()), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        new=AsyncMock(side_effect=_overlap_tracker(state, {"sessionID": "0xsid"})),
    ):
        await asyncio.gather(
            *[service._open_session_for_model(model_id=f"0xmodel{i}") for i in range(6)]
        )
    assert not service._is_throttled()
    assert state["max"] > 1, "happy-path opens must NOT be serialized"


# ---------------------------------------------------------------------------
# Nonce conflict -> engage throttle + serialized retry of the failing op.
# ---------------------------------------------------------------------------


async def test_nonce_conflict_engages_throttle_and_retries_serialized(service):
    open_mock = AsyncMock(side_effect=[
        ProxyRouterServiceError("HTTP 500: replacement transaction underpriced"),
        {"sessionID": "0xrecovered"},
    ])
    with _patch_get_db(_make_db()), patch(
        "src.services.session_routing_service.proxy_router_service.openSession", new=open_mock
    ), patch("src.services.session_routing_service.asyncio.sleep", new=AsyncMock()):
        sid = await service._open_session_for_model(model_id="0xmodel", initial_active_requests=1)

    assert sid == "0xrecovered", "the op must be rescued by the serialized retry"
    assert open_mock.await_count == 2, "exactly one serialized retry"
    assert service._is_throttled(), "a nonce conflict must arm the throttle window"


async def test_non_nonce_error_does_not_throttle_or_retry(service):
    open_mock = AsyncMock(side_effect=ProxyRouterServiceError("no provider accepting session"))
    with _patch_get_db(_make_db()), patch(
        "src.services.session_routing_service.proxy_router_service.openSession", new=open_mock
    ):
        with pytest.raises(Exception):
            await service._open_session_for_model(model_id="0xmodel")

    assert open_mock.await_count == 1, "non-nonce errors must not be retried"
    assert not service._is_throttled(), "non-nonce errors must not arm the throttle"


# ---------------------------------------------------------------------------
# While throttled: serialize (drain the burst in nonce order), opens AND closes.
# ---------------------------------------------------------------------------


async def test_throttled_mode_serializes_opens(service):
    service._throttle_until = time.monotonic() + 100  # force THROTTLED
    state = {"cur": 0, "max": 0}
    with _patch_get_db(_make_db()), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        new=AsyncMock(side_effect=_overlap_tracker(state, {"sessionID": "0xsid"})),
    ):
        await asyncio.gather(
            *[service._open_session_for_model(model_id=f"0xmodel{i}") for i in range(6)]
        )
    assert state["max"] == 1, "throttled opens must serialize on the wallet lock"


async def test_throttled_mode_open_and_close_share_lock(service):
    service._throttle_until = time.monotonic() + 100
    state = {"cur": 0, "max": 0}
    with _patch_get_db(_make_db()), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        new=AsyncMock(side_effect=_overlap_tracker(state, {"sessionID": "0xsid"})),
    ), patch(
        "src.services.session_routing_service.proxy_router_service.closeSession",
        new=AsyncMock(side_effect=_overlap_tracker(state, {"success": True})),
    ):
        await asyncio.gather(
            service._open_session_for_model(model_id="0xmodel"),
            service._close_invalidated_session("0xdead"),
        )
    assert state["max"] == 1, "throttled open and close share the wallet lock"


# ---------------------------------------------------------------------------
# No DB connection is held across the on-chain open (so a throttled queue
# cannot exhaust the pool).
# ---------------------------------------------------------------------------


async def test_no_db_connection_held_during_onchain_open(service):
    db_entered_during_open = {"value": False}
    open_in_flight = {"value": False}

    async def _open(*args, **kwargs):
        open_in_flight["value"] = True
        await asyncio.sleep(0.01)
        open_in_flight["value"] = False
        return {"sessionID": "0xnew"}

    @asynccontextmanager
    async def _tracking_get_db():
        if open_in_flight["value"]:
            db_entered_during_open["value"] = True
        yield _make_db()

    with patch("src.services.session_routing_service.get_db", _tracking_get_db), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        new=AsyncMock(side_effect=_open),
    ):
        await service._open_session_for_model(model_id="0xmodel")

    assert db_entered_during_open["value"] is False, (
        "a DB connection was checked out while the on-chain open was in flight"
    )


# ---------------------------------------------------------------------------
# Create-assigned: request-path sessions are persisted with active_requests=1.
# ---------------------------------------------------------------------------


async def test_request_path_session_created_already_assigned(service):
    db = _make_db()
    added = []
    db.add = MagicMock(side_effect=added.append)

    with _patch_get_db(db), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        new=AsyncMock(return_value={"sessionID": "0xnew"}),
    ):
        sid = await service._open_session_for_model(
            model_id="0xmodel", initial_active_requests=1
        )

    assert sid == "0xnew"
    assert len(added) == 1
    assert added[0].active_requests == 1, "request-path session must be created already assigned"
    assert added[0].state == SessionState.OPEN


async def test_automation_path_session_created_idle(service):
    db = _make_db()
    added = []
    db.add = MagicMock(side_effect=added.append)

    with _patch_get_db(db), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        new=AsyncMock(return_value={"sessionID": "0xidle"}),
    ):
        await service._open_session_for_model(model_id="0xmodel")  # default 0

    assert added[0].active_requests == 0, "automation pre-warm session must be idle"
