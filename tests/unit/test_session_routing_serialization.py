"""Tests for per-replica on-chain wallet serialization in SessionRoutingService.

A single consumer wallet signs every on-chain open/closeSession, so concurrent
txs collide on nonce ("replacement transaction underpriced"). The service
serializes all on-chain session ops on a replica with one wallet lock, while
holding NO DB connection during the wait — so any number of concurrent openers
can queue on the lock without pinning the DB pool. New request-path sessions are
created already assigned (active_requests=1) so they are never momentarily
claimable by another request. These tests pin that behavior.
"""
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.db.models import SessionState
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
    """Patch the module-level get_db() so route paths use a mocked connection."""
    @asynccontextmanager
    async def _fake_get_db():
        yield db
    return patch("src.services.session_routing_service.get_db", _fake_get_db)


def _overlap_tracker(state, return_value):
    """An async stand-in for an on-chain call that records peak concurrency."""

    async def _call(*args, **kwargs):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.01)  # force a scheduling point mid-call
        state["cur"] -= 1
        return return_value

    return _call


# ---------------------------------------------------------------------------
# Serialization: one on-chain tx at a time, ACROSS models (nonce is per-wallet,
# not per-model — the old per-model lock allowed cross-model collisions).
# ---------------------------------------------------------------------------


async def test_open_session_serializes_onchain_across_models(service):
    state = {"cur": 0, "max": 0}
    with _patch_get_db(_make_db()), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        new=AsyncMock(side_effect=_overlap_tracker(state, {"sessionID": "0xsid"})),
    ):
        await asyncio.gather(
            *[service._open_session_for_model(model_id=f"0xmodel{i}") for i in range(8)]
        )
    assert state["max"] == 1, "concurrent opens for different models must not overlap on-chain"


async def test_open_and_close_share_one_wallet_lock(service):
    """An open and a background close must not issue overlapping on-chain txs."""
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
    assert state["max"] == 1, "open and close share the wallet and must serialize"


async def test_unbounded_openers_queue_without_a_cap(service):
    """Many more concurrent openers than any old cap all complete (no shedding)."""
    state = {"cur": 0, "max": 0, "done": 0}

    async def _open(*args, **kwargs):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.001)
        state["cur"] -= 1
        state["done"] += 1
        return {"sessionID": "0xsid"}

    with _patch_get_db(_make_db()), patch(
        "src.services.session_routing_service.proxy_router_service.openSession",
        new=AsyncMock(side_effect=_open),
    ):
        results = await asyncio.gather(
            *[service._open_session_for_model(model_id="0xmodel") for _ in range(50)]
        )

    assert state["done"] == 50, "every queued opener must complete; none shed"
    assert all(r == "0xsid" for r in results)
    assert state["max"] == 1, "still strictly serialized on the wallet lock"


# ---------------------------------------------------------------------------
# No DB connection is acquired while the on-chain open is in flight (so an
# unbounded queue cannot exhaust the pool).
# ---------------------------------------------------------------------------


async def test_no_db_connection_held_during_onchain_open(service):
    """get_db() must not be entered until AFTER openSession returns."""
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
