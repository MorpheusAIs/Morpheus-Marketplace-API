"""Video clip sessions: never closed before EndsAt by mistake, never stranded open.

A provider withholds an undelivered clip when its session closes more than
60 s before EndsAt, so expiry cleanup must not run ahead of EndsAt. A failed
close must stay retryable, or the session day-locks its whole stake unmetered.
"""

import asyncio
import importlib
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/test_db",
)

import pytest
from sqlalchemy.orm.evaluator import _EvaluatorCompiler
from sqlalchemy.sql.dml import Update

from src.core.config import settings
from src.db.models.routed_session import RoutedSession, SessionState
from src.services import proxy_router_service
from src.services.session_routing_service import (
    SessionRoutingService,
    VIDEO_CLOSE_ABANDONED_SECONDS,
    VIDEO_CLOSE_EXPIRY_MARGIN_SECONDS,
    VIDEO_JOBS_ENDPOINT,
)

# The module, not the session_routing_service instance src.services exports
srs = importlib.import_module("src.services.session_routing_service")

MODEL_ID = "0x" + "5e" * 32
SESSION_ID = "0x" + "5a" * 32
NOW = datetime(2026, 11, 6, 12, 0, 0)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        result = MagicMock()
        result.all.return_value = list(self._rows)
        return result


class FakeDb:
    """routed_sessions in memory. WHERE clauses run through SQLAlchemy's ORM
    evaluator, so the conditional claim (state = 'OPEN') behaves as in SQL."""

    def __init__(self, *rows):
        self.rows = {row.id: row for row in rows}
        # An UPDATE to this state fails, as with the database unreachable
        self.refused_state = None

    @asynccontextmanager
    async def get_db(self):
        yield self

    async def connection(self):
        """The real session checks out a connection here; nothing to do in memory."""
        return None

    async def execute(self, stmt):
        match = _EvaluatorCompiler(RoutedSession).process(stmt.whereclause)
        hits = [row for row in self.rows.values() if match(row)]
        if isinstance(stmt, Update):
            values = {column.key: value.value for column, value in stmt._values.items()}
            if self.refused_state is not None and values.get("state") == self.refused_state:
                raise ConnectionError("database unavailable")
            for row in hits:
                for key, value in values.items():
                    setattr(row, key, value)
            return _Result([row.id for row in hits])
        selected = stmt.column_descriptions[0]
        if selected["expr"] is RoutedSession:
            return _Result(hits)
        return _Result([getattr(row, selected["name"]) for row in hits])

    def add(self, row):
        self.rows[row.id] = row

    async def commit(self):
        pass


def _video_session(state=SessionState.OPEN.value):
    return RoutedSession(
        id=SESSION_ID, model_id=MODEL_ID, model_name="seedance-2.5",
        state=state, active_requests=1, endpoint=VIDEO_JOBS_ENDPOINT,
        created_at=NOW, updated_at=NOW, expires_at=NOW + timedelta(seconds=660),
    )


CLOSED_ON_CHAIN = {"ok": True}
RPC_DOWN = proxy_router_service.ProxyRouterServiceError(
    "Failed to close session: 502 Bad Gateway", status_code=502, error_type="server_error",
)
# Shutdown or a sweeper timeout cancels the close mid-call
CANCELLED = asyncio.CancelledError()
# The chain never answers: close_session_now's own deadline ends the call
HANGS = "hangs"
CLOSE_DEADLINE = 0.2
IN_FLIGHT = timedelta(minutes=1)
ABANDONED = timedelta(seconds=VIDEO_CLOSE_ABANDONED_SECONDS + 60)
LIVE = timedelta(seconds=660)
EXPIRED = timedelta(seconds=-1)
# Not expired yet, but the expiry cleanup may already have read it OPEN
EXPIRING = timedelta(seconds=VIDEO_CLOSE_EXPIRY_MARGIN_SECONDS - 10)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _chain(outcomes):
    """closeSession answering each call with the next outcome."""
    outcomes = list(outcomes)

    async def close_session(session_id):
        outcome = outcomes.pop(0)
        if outcome == HANGS:
            await asyncio.Event().wait()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return AsyncMock(side_effect=close_session)


async def _close_now(service):
    try:
        # A close that outlives its own deadline fails the test instead of hanging it
        return await asyncio.wait_for(service.close_session_now(SESSION_ID, reason="video_completed"), timeout=5)
    except asyncio.CancelledError as e:
        return e


@pytest.mark.parametrize(
    "start_state, idle_for, expires_in, chain_outcomes, returns, end_state, chain_calls, expiry_cleanup_closes",
    [
        (SessionState.OPEN.value, IN_FLIGHT, LIVE, [CLOSED_ON_CHAIN], [True], "CLOSED", 1, False),
        # A failed close goes back to OPEN: the next call reaches the chain again
        (SessionState.OPEN.value, IN_FLIGHT, LIVE, [RPC_DOWN, CLOSED_ON_CHAIN], [False, True], "CLOSED", 2, False),
        # Still failing: OPEN, so expiry cleanup closes it at expires_at
        (SessionState.OPEN.value, IN_FLIGHT, LIVE, [RPC_DOWN, RPC_DOWN], [False, False], "OPEN", 2, True),
        # A cancelled close is never left CLOSING: back to OPEN, still retryable
        (SessionState.OPEN.value, IN_FLIGHT, LIVE, [CANCELLED], [CANCELLED], "OPEN", 1, True),
        # A chain that never answers is given up at the deadline: back to OPEN
        (SessionState.OPEN.value, IN_FLIGHT, LIVE, [HANGS], [False], "OPEN", 1, True),
        # A close already in flight is never doubled
        (SessionState.CLOSING.value, IN_FLIGHT, LIVE, [], [False], "CLOSING", 0, False),
        # A close whose closer died is claimed again, expired or not: the
        # expiry cleanup never takes a CLOSING row
        (SessionState.CLOSING.value, ABANDONED, LIVE, [CLOSED_ON_CHAIN], [True], "CLOSED", 1, False),
        (SessionState.CLOSING.value, ABANDONED, EXPIRED, [CLOSED_ON_CHAIN], [True], "CLOSED", 1, False),
        # An expired or expiring session is left to the expiry cleanup, so the
        # two never close and meter it together
        (SessionState.OPEN.value, IN_FLIGHT, EXPIRED, [], [False], "OPEN", 0, True),
        (SessionState.OPEN.value, IN_FLIGHT, EXPIRING, [], [False], "OPEN", 0, True),
    ],
)
@pytest.mark.asyncio
async def test_video_session_close_stays_retryable(
    start_state, idle_for, expires_in, chain_outcomes, returns, end_state, chain_calls, expiry_cleanup_closes,
):
    row = _video_session(start_state)
    row.updated_at = _utcnow() - idle_for
    row.expires_at = _utcnow() + expires_in
    db = FakeDb(row)
    service = SessionRoutingService()
    close = _chain(chain_outcomes)
    with patch.object(srs, "get_db", db.get_db), \
            patch.object(srs, "VIDEO_CLOSE_DEADLINE_SECONDS", CLOSE_DEADLINE), \
            patch.object(srs.proxy_router_service, "closeSession", close), \
            patch.object(service, "_read_daylock_on_close", AsyncMock(return_value=None)), \
            patch.object(service, "_record_daylock_on_close", AsyncMock()):
        # The sweeper reclaims exactly the closes whose closer died
        reclaimed = await service.list_abandoned_video_closes(limit=5)
        assert (reclaimed == [SESSION_ID]) is (start_state == SessionState.CLOSING.value and idle_for == ABANDONED)

        got = [await _close_now(service) for _ in returns]
        state = await service.get_session_state(SESSION_ID)

        assert got == returns
        assert state == end_state
        assert close.await_count == chain_calls
        if end_state == "OPEN" and chain_calls:
            assert row.active_requests == 0
            assert row.error_reason.startswith("Close failed:")

        close.side_effect = None
        close.return_value = CLOSED_ON_CHAIN
        row.expires_at = min(row.expires_at, _utcnow() - timedelta(seconds=1))
        await service._cleanup_expired_sessions(db)
        assert (row.state == SessionState.EXPIRED.value) is expiry_cleanup_closes


RECLAIMED = "reclaimed"
CLOSED_MARK_FAILS = "closed_mark_fails"
RECLAIM_STAMP = NOW + timedelta(hours=1)
DAYLOCK = (0.5, 1.0, "receipt")


@pytest.mark.parametrize(
    "chain_outcome, meanwhile, returns, end_state",
    [
        # The premium daylock is counted once, after the CLOSED mark
        (CLOSED_ON_CHAIN, None, True, "CLOSED"),
        # Another call claimed the row again mid-close (this one was taken for
        # dead): the old closer's CLOSED mark or revert to OPEN writes nothing,
        # so the row stays with its new closer and no third caller can claim it
        (CLOSED_ON_CHAIN, RECLAIMED, False, "CLOSING"),
        (RPC_DOWN, RECLAIMED, False, "CLOSING"),
        (CANCELLED, RECLAIMED, CANCELLED, "CLOSING"),
        # A CLOSED mark that did not land is no success, and counts nothing
        (CLOSED_ON_CHAIN, CLOSED_MARK_FAILS, False, "CLOSING"),
    ],
)
@pytest.mark.asyncio
async def test_video_session_close_writes_only_under_its_own_claim(chain_outcome, meanwhile, returns, end_state):
    row = _video_session()
    row.expires_at = _utcnow() + LIVE
    db = FakeDb(row)
    service = SessionRoutingService()

    async def close_session(session_id):
        if meanwhile == RECLAIMED:
            row.updated_at = RECLAIM_STAMP
        elif meanwhile == CLOSED_MARK_FAILS:
            db.refused_state = SessionState.CLOSED.value
        if isinstance(chain_outcome, BaseException):
            raise chain_outcome
        return chain_outcome

    counted = AsyncMock()
    with patch.object(srs, "get_db", db.get_db), \
            patch.object(srs.proxy_router_service, "closeSession", AsyncMock(side_effect=close_session)), \
            patch.object(srs.settings, "SESSION_PREMIUM_DAILY_BUDGET_MOR", 100.0), \
            patch.object(service, "_is_premium_model", return_value=True), \
            patch.object(service, "_read_daylock_on_close", AsyncMock(return_value=DAYLOCK)), \
            patch.object(service, "_add_premium_daylock_spent", counted):
        got = await _close_now(service)

    assert got == returns
    assert row.state == end_state
    if meanwhile == RECLAIMED:
        assert row.updated_at == RECLAIM_STAMP
    assert counted.await_count == (returns is True)
    assert (row.daylock_mor is not None) == (returns is True)


class _Clock(datetime):
    """The module's datetime, with now() under test control."""

    current = NOW.replace(tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current


OPEN_TAKES = timedelta(seconds=300)
QUOTE = {
    "bid_id": "0x" + "b1" * 32, "provider": "0x" + "a1" * 20,
    "price_per_second_wei": str(10**15), "session_seconds": 600,
}
CHAT_BID = {"bid_id": "0x" + "b2" * 32, "provider": "0x" + "a2" * 20, "stake_mor": 1.0, "pps": 0.001}


async def _open_video(service):
    session_id, _ = await service.open_session_for_quote(
        model_id=MODEL_ID, model_name="seedance-2.5", candidates=[QUOTE],
    )
    return session_id


async def _open_chat(service):
    return await service._open_session_for_model(MODEL_ID, model_name="llama", initial_active_requests=1)


@pytest.mark.parametrize(
    "open_session, expected_expires_at",
    [
        # Video: from when the open returned, an upper bound on EndsAt, plus the buffer
        (_open_video, NOW + OPEN_TAKES
         + timedelta(seconds=QUOTE["session_seconds"] + settings.SESSION_EXPIRY_BUFFER_SECONDS)),
        # Chat: unchanged, from the start of the walk
        (_open_chat, NOW + timedelta(seconds=settings.SESSION_DEFAULT_DURATION_SECONDS)),
    ],
)
@pytest.mark.asyncio
async def test_video_session_fallback_expiry_is_after_ends_at(open_session, expected_expires_at):
    """The status read after the open fails, so expires_at is the fallback."""
    db = FakeDb()
    service = SessionRoutingService()
    _Clock.current = NOW.replace(tzinfo=timezone.utc)

    async def slow_open(*, bid_id, session_duration):
        _Clock.current += OPEN_TAKES
        return {"sessionID": SESSION_ID}

    policy = MagicMock(max_stake_mor=0)
    gates = (
        "_ensure_allowlist_allows_open", "_ensure_soft_cap_allows_open",
        "_ensure_mor_low_water_allows_open", "_ensure_max_bid_pps_allows_open",
        "_ensure_premium_budget_allows_open",
    )
    with patch.object(srs, "get_db", db.get_db), \
            patch.object(srs, "datetime", _Clock), \
            patch.object(srs, "get_session_routing_policy", return_value=policy), \
            patch.object(srs.proxy_router_service, "openSessionByBid", slow_open), \
            patch.object(srs.proxy_router_service, "getSessionStatus",
                         AsyncMock(side_effect=RPC_DOWN)), \
            patch.object(service, "_is_expensive_model", AsyncMock(return_value=False)), \
            patch.object(service, "_list_bids_under_fuse", AsyncMock(return_value=[CHAT_BID])), \
            patch.multiple(service, **{gate: AsyncMock() for gate in gates}):
        session_id = await open_session(service)

    assert db.rows[session_id].expires_at == expected_expires_at
