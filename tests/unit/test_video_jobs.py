"""Video job state machine: the refresh transition table, the submit failure
paths, idempotency, and the sweeper's stuck-submit, close-retry and
stranded-hold rules.

Money rules: a clip is charged only when the node delivers it; every other
end voids the hold. A clip's session is closed once the clip is terminal,
and never while the node may still render it (the provider withholds a clip
whose session closes more than 60 s before EndsAt).
"""

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/test_db",
)

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.evaluator import UnevaluatableError, _EvaluatorCompiler
from sqlalchemy.sql.dml import Update

from src.core.config import settings
from src.db.models import VideoJob
from src.schemas.billing import UsageFinalizeResponse, UsageHoldResponse, UsageVoidResponse
from src.services.pricing.types import ClipPricing
from src.services.proxy_router_service import ProxyRouterServiceError
from src.services.session_routing_service import SessionOpenError, SessionPoolBusyError
from src.services.video import jobs
from src.services.video.params import VideoRequest, to_video_object

USER_ID = 7
MODEL_ID = "0x" + "5e" * 32
BID_ID = "0x" + "b1" * 32
SESSION_ID = "0x" + "5a" * 32
VIDEO_URL = "https://vendor.example/clip.mp4?sig=bearer"


class _Result:
    def __init__(self, rows, rowcount=0):
        self._rows = rows
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class FakeDb:
    """video_jobs in memory. WHERE clauses run through SQLAlchemy's ORM
    evaluator, so a conditional update (status = 'pending') behaves as in SQL.
    Other tables are empty (the stranded-hold ledger join finds nothing), and
    an insert that repeats a user's idempotency key fails on commit."""

    def __init__(self, *rows):
        self.rows = {row.id: row for row in rows}
        self._duplicate = False

    @asynccontextmanager
    async def get_db(self):
        yield self

    async def connection(self):
        """The real session checks out a connection here; nothing to do in memory."""
        return None

    async def execute(self, stmt):
        try:
            match = _EvaluatorCompiler(VideoJob).process(stmt.whereclause)
        except UnevaluatableError:
            return _Result([])
        hits = [row for row in self.rows.values() if match(row)]
        if isinstance(stmt, Update):
            for row in hits:
                for column, value in stmt._values.items():
                    setattr(row, column.key, value.value)
            return _Result([], rowcount=len(hits))
        columns = stmt.column_descriptions
        if columns[0]["expr"] is VideoJob:
            return _Result(hits)
        if len(columns) == 1:
            return _Result([getattr(row, columns[0]["name"]) for row in hits])
        return _Result([SimpleNamespace(**{c["name"]: getattr(row, c["name"]) for c in columns}) for row in hits])

    def add(self, row):
        self._duplicate = bool(row.idempotency_key) and any(
            (other.user_id, other.idempotency_key) == (row.user_id, row.idempotency_key)
            for other in self.rows.values()
        )
        if not self._duplicate:
            self.rows[row.id] = row

    async def commit(self):
        if self._duplicate:
            self._duplicate = False
            raise IntegrityError("INSERT INTO video_jobs", {}, Exception("duplicate key"))


@pytest.fixture(autouse=True)
def _no_abandoned_closes():
    """routed_sessions is not in FakeDb: no video session is left CLOSING."""
    with patch.object(jobs.session_routing_service, "list_abandoned_video_closes", AsyncMock(return_value=[])):
        yield


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _row(**overrides):
    now = _utcnow()
    values = dict(
        id=jobs.new_video_id(), user_id=USER_ID, api_key_id=1,
        model_name="seedance-2.5", model_id=MODEL_ID, prompt="a cat on a skateboard",
        duration_seconds=10, resolution="1080p", aspect_ratio="16:9",
        generate_audio=True, has_image=False,
        status="in_progress", price_usd=Decimal("2.70000000"),
        ledger_entry_id=uuid.uuid4(), billing_state="held",
        session_id=SESSION_ID, bid_id=BID_ID, node_job_id="vj_1", node_misses=0,
        created_at=now - timedelta(minutes=1), updated_at=now,
    )
    values.update(overrides)
    return VideoJob(**values)


def _billing_mocks():
    settle = AsyncMock(side_effect=lambda db, user_id, ledger_entry_id, commit=True: UsageFinalizeResponse(
        ledger_entry_id=ledger_entry_id, amount_paid=Decimal("-2.7"), amount_staking=Decimal("0"),
        amount_total=Decimal("-2.7"), success=True,
    ))
    void = AsyncMock(side_effect=lambda db, user_id, request, commit=True: UsageVoidResponse(
        ledger_entry_id=request.ledger_entry_id, voided=True,
    ))
    return settle, void


async def _drain_background_tasks():
    while jobs._background_tasks:
        await asyncio.gather(*list(jobs._background_tasks), return_exceptions=True)


NODE_404 = ProxyRouterServiceError("video job not found", status_code=404, error_type="client_error",
                                   node_error_body=True)
# A 404 page from a proxy in front of the node, not the node's {"error": ...}
PROXY_404 = ProxyRouterServiceError("<html>404 Not Found</html>", status_code=404, error_type="client_error")
NODE_DOWN = ProxyRouterServiceError("timed out", status_code=None, error_type="network_error")
SUCCEEDED = {"status": "succeeded", "video_url": VIDEO_URL, "expires_at": "2099-11-07T10:00:00.123456789Z"}
LINK_EXPIRED = {"status": "succeeded", "video_url": VIDEO_URL, "expires_at": "2026-01-01T00:00:00Z"}
RUNNING = {"status": "running"}
FRESH = timedelta(minutes=1)
TOO_OLD = timedelta(seconds=settings.VIDEO_JOB_MAX_AGE_SECONDS + 60)
SWEEP, GET = False, True


@pytest.mark.parametrize(
    "node_read, client_read, row_status, age, misses, billing, failure_code, status, error_code, session_closed",
    [
        (SUCCEEDED, SWEEP, "in_progress", FRESH, 0, "settle", None, "completed", None, True),
        ({"status": "failed", "error": {"code": "content_policy", "message": "refused"}},
         SWEEP, "in_progress", FRESH, 0, "void", "job_failed", "failed", "content_policy", True),
        ({"status": "expired", "error": None}, SWEEP, "in_progress", FRESH, 0,
         "void", "job_expired", "failed", "expired", True),
        ({"status": "cancelled", "error": {"code": "session_closed_early", "message": "withheld"}},
         SWEEP, "queued", FRESH, 0, "void", "job_cancelled", "failed", "session_closed_early", True),
        (RUNNING, SWEEP, "queued", FRESH, 0, None, None, "in_progress", None, False),
        (RUNNING, SWEEP, "in_progress", TOO_OLD, 0, "void", "timeout", "failed", "timeout", True),
        # A clip the node finished late is still delivered and charged
        (SUCCEEDED, SWEEP, "in_progress", TOO_OLD, 0, "settle", None, "completed", None, True),
        # A link the user can no longer fetch is never charged
        (LINK_EXPIRED, SWEEP, "in_progress", FRESH, 0, "void", "job_link_expired", "failed", "video_expired", True),
        (NODE_404, SWEEP, "in_progress", FRESH, 1, None, None, "in_progress", None, False),
        (NODE_404, SWEEP, "in_progress", FRESH, 2, "void", "node_lost", "failed", "node_lost", True),
        # Only the sweeper counts misses: GET polling cannot lose a rendering clip
        (NODE_404, GET, "in_progress", FRESH, 2, None, None, "in_progress", None, False),
        # Only the node's own 404 counts: a proxy page proves nothing
        (PROXY_404, SWEEP, "in_progress", FRESH, 2, None, None, "in_progress", None, False),
        # A node blip never fails a rendering clip
        (NODE_DOWN, SWEEP, "in_progress", FRESH, 0, None, None, "in_progress", None, False),
    ],
)
async def test_refresh_transition(
    node_read, client_read, row_status, age, misses, billing, failure_code, status, error_code, session_closed
):
    row = _row(status=row_status, node_misses=misses, created_at=_utcnow() - age)
    db = FakeDb(row)
    settle, void = _billing_mocks()
    read = AsyncMock(side_effect=node_read) if isinstance(node_read, Exception) else AsyncMock(return_value=node_read)
    close = AsyncMock(return_value=True)

    with patch.object(jobs, "get_db", db.get_db), \
            patch("src.services.proxy_router_service.getVideoJob", read), \
            patch.object(jobs.billing_service, "settle_fixed_hold", settle), \
            patch.object(jobs.billing_service, "void_usage", void), \
            patch.object(jobs.session_routing_service, "close_session_now", close):
        await jobs.refresh_job(row.id, client_read=client_read)
        await _drain_background_tasks()

    assert row.status == status
    assert settle.await_count == (billing == "settle")
    assert void.await_count == (billing == "void")
    assert row.billing_state == {"settle": "charged", "void": "voided", None: "held"}[billing]
    if billing == "void":
        assert void.await_args.args[2].failure_code == failure_code
    if billing == "settle":
        assert settle.await_args.args[2] == row.ledger_entry_id
        assert row.video_url == VIDEO_URL
        assert row.expires_at == datetime(2099, 11, 7, 10, 0, 0, 123456)
    else:
        assert row.video_url is None
    assert row.error_code == error_code
    if session_closed:
        close.assert_awaited_once()
        assert close.await_args.args == (SESSION_ID,)
        assert row.session_closed_at is not None
    else:
        close.assert_not_awaited()
        assert row.session_closed_at is None


@pytest.mark.parametrize(
    "last_read_ago, node_read",
    [
        (1.0, False),
        (jobs.REFRESH_MIN_INTERVAL_SECONDS + 0.5, True),
    ],
)
async def test_get_skips_the_node_read_right_after_another(last_read_ago, node_read):
    row = _row(status="in_progress")
    jobs._last_node_reads[row.id] = time.monotonic() - last_read_ago
    read = AsyncMock(return_value=RUNNING)

    with patch.object(jobs, "get_db", FakeDb(row).get_db), \
            patch("src.services.proxy_router_service.getVideoJob", read):
        await jobs.refresh_job(row.id, client_read=True)

    assert read.await_count == node_read


def _quote_entry():
    return {
        "provider": "0x" + "9f" * 20, "bid_id": BID_ID, "price_per_second_wei": "1000000000000000",
        "session_seconds": 900, "quote_seconds": 843, "session_cost_wei": "900000000000000000", "error": "",
        "resolved": {"prompt": "a cat on a skateboard", "duration_seconds": 10, "aspect_ratio": "16:9",
                     "resolution": "1080p", "generate_audio": True, "has_image": False},
    }


PRICING = ClipPricing(
    model_key="seedance-2.5",
    per_second={"480p": Decimal("0.05"), "720p": Decimal("0.10"), "1080p": Decimal("0.25")},
    audio_per_second=Decimal("0.02"),
)
REQUEST = VideoRequest(model="seedance-2.5", prompt="a cat on a skateboard",
                       duration_seconds=10, resolution="1080p", generate_audio=True)


def _submit(idempotency_key=None):
    return jobs.submit_video(
        user_id=USER_ID, api_key_id=1, model_id=MODEL_ID, model_name="seedance-2.5",
        request=REQUEST, request_id="req1", idempotency_key=idempotency_key,
    )


def _quote_patches(db, hold):
    return [
        patch.object(jobs, "get_db", db.get_db),
        patch("src.services.proxy_router_service.videoQuote",
              AsyncMock(return_value={"object": "list", "data": [_quote_entry()]})),
        patch.object(jobs.direct_model_service, "get_healthy_bid_ids_for_model", AsyncMock(return_value={BID_ID})),
        patch.object(jobs.billing_service.pricing, "get_clip_pricing", AsyncMock(return_value=PRICING)),
        patch.object(jobs.billing_service, "create_fixed_hold", hold),
    ]


@pytest.mark.parametrize(
    "open_error, submit_error, error_code, failure_code, session_closed",
    [
        (SessionPoolBusyError("pool busy", retry_after=15), None, "session_open_failed", "session_open_failed", False),
        (SessionOpenError("Failed to open session: nonce"), None, "session_open_failed", "session_open_failed", False),
        # The node itself refused ({"error": ...}): it kept nothing, the empty session closes now
        (None, ProxyRouterServiceError('HTTP 422: {"error":"provider refused: session too short"}', 422,
                                       "client_error"), "node_error", "node_refused_422", True),
        (None, ProxyRouterServiceError('HTTP 502: {"error":"provider unreachable"}', 502, "server_error"),
         "node_error", "node_refused_502", True),
        # The node may hold the clip: its session is left to the expiry close at EndsAt
        (None, ProxyRouterServiceError("Request failed after 1 attempts: timed out", None, "network_error"),
         "submit_interrupted", "node_error", False),
        # A 502 page from a proxy in front of the node, JSON without the node's
        # string error, or an empty 503, is no refusal
        (None, ProxyRouterServiceError("HTTP 502: <html>502 Bad Gateway</html>", 502, "server_error"),
         "submit_interrupted", "node_error", False),
        (None, ProxyRouterServiceError('HTTP 502: {"message":"Bad Gateway"}', 502, "server_error"),
         "submit_interrupted", "node_error", False),
        (None, ProxyRouterServiceError('HTTP 503: {"error":{"message":"upstream unavailable"}}', 503,
                                       "server_error"), "submit_interrupted", "node_error", False),
        (None, ProxyRouterServiceError("HTTP 503: ", 503, "server_error"), "submit_interrupted", "node_error", False),
    ],
)
async def test_submit_answers_queued_then_a_failure_voids_the_hold(
    open_error, submit_error, error_code, failure_code, session_closed
):
    db = FakeDb()
    _, void = _billing_mocks()
    hold = AsyncMock(return_value=UsageHoldResponse(
        success=True, ledger_entry_id=uuid.uuid4(), hold_amount=Decimal("-2.7"), estimated_cost=Decimal("2.7"),
    ))
    open_session = AsyncMock(side_effect=open_error) if open_error else AsyncMock(
        return_value=(SESSION_ID, _quote_entry()))
    submit = AsyncMock(side_effect=submit_error)
    close = AsyncMock(return_value=True)

    patches = _quote_patches(db, hold) + [
        # The node's raw answer, so the client decides whether the node itself answered
        patch("src.services.proxy_router_service._execute_request", submit),
        patch.object(jobs.billing_service, "void_usage", void),
        patch.object(jobs.session_routing_service, "open_session_for_quote", open_session),
        patch.object(jobs.session_routing_service, "close_session_now", close),
    ]
    for p in patches:
        p.start()
    try:
        job = await _submit()
        # The answer leaves before the session opens
        assert to_video_object(job)["status"] == "queued"
        open_session.assert_not_awaited()
        await _drain_background_tasks()
    finally:
        for p in patches:
            p.stop()

    (row,) = db.rows.values()
    assert hold.await_args.args[2].amount == Decimal("2.70000000")
    assert row.status == "failed"
    assert row.error_code == error_code
    assert row.billing_state == "voided"
    void.assert_awaited_once()
    assert void.await_args.args[2].ledger_entry_id == row.ledger_entry_id
    assert void.await_args.args[2].failure_code == failure_code
    assert submit.await_count == (open_error is None)
    assert row.session_id == (None if open_error else SESSION_ID)
    if session_closed:
        close.assert_awaited_once()
        assert close.await_args.args == (SESSION_ID,)
        assert row.session_closed_at is not None
    else:
        close.assert_not_awaited()
        assert row.session_closed_at is None


async def test_concurrent_duplicate_key_returns_the_first_video_and_holds_nothing():
    first = _row(status="pending", billing_state="held", idempotency_key="key-1", session_id=None, node_job_id=None)
    db = FakeDb(first)
    hold = AsyncMock()

    patches = _quote_patches(db, hold)
    for p in patches:
        p.start()
    try:
        job = await _submit(idempotency_key="key-1")
    finally:
        for p in patches:
            p.stop()

    assert job is first
    hold.assert_not_awaited()
    assert list(db.rows) == [first.id]
    assert not jobs._background_tasks


STALE = timedelta(seconds=jobs.pending_timeout_seconds() + 60)
SUBMITTING = timedelta(seconds=settings.PROXY_ROUTER_VIDEO_SUBMIT_TIMEOUT)


async def _sweep_twice(row):
    await jobs.sweep()
    # The next pass retries the closes of terminal rows
    await jobs.sweep()


async def _interrupt_found_stale(row):
    """The pass found the row stale, then its session open finished."""
    await jobs._interrupt_pending(row.id)


@pytest.mark.parametrize(
    "session_id, quiet_for, status, run",
    [
        (None, STALE, "failed", _sweep_twice),
        # The node may hold the clip: the session is never closed early
        (SESSION_ID, STALE, "failed", _sweep_twice),
        # Created long ago, but its node submit only started: still in flight
        (SESSION_ID, SUBMITTING, "pending", _sweep_twice),
        # Staleness is checked again under the row lock
        (SESSION_ID, FRESH, "pending", _interrupt_found_stale),
    ],
)
async def test_stuck_submit_is_failed_and_its_session_left_to_expire(session_id, quiet_for, status, run):
    now = _utcnow()
    row = _row(status="pending", billing_state="held", session_id=session_id, node_job_id=None,
               created_at=now - timedelta(hours=2), updated_at=now - quiet_for)
    db = FakeDb(row)
    _, void = _billing_mocks()
    close = AsyncMock(return_value=True)

    with patch.object(jobs, "get_db", db.get_db), \
            patch.object(jobs.billing_service, "void_usage", void), \
            patch.object(jobs.session_routing_service, "close_session_now", close), \
            patch.object(jobs.session_routing_service, "get_session_state", AsyncMock(return_value="OPEN")):
        await run(row)

    assert row.status == status
    if status == "failed":
        assert row.error_code == "submit_interrupted"
        void.assert_awaited_once()
        assert void.await_args.args[2].failure_code == "submit_interrupted"
        assert row.billing_state == "voided"
    else:
        void.assert_not_awaited()
    close.assert_not_awaited()
    assert row.session_closed_at is None


CLIP_ENDED = dict(error_code="job_failed")
SUBMIT_UNCONFIRMED = dict(error_code="submit_interrupted", node_job_id=None)
HANGS = "hangs"


async def _hang(*args, **kwargs):
    await asyncio.Event().wait()


@pytest.mark.parametrize(
    "row_values, closed_now, routed_state, close_sent, stamped",
    [
        (CLIP_ENDED, True, None, True, True),
        (CLIP_ENDED, False, "CLOSED", True, True),
        (CLIP_ENDED, False, "EXPIRED", True, True),
        # The on-chain close failed and the row went back OPEN, or another
        # close is still running: the next pass retries
        (CLIP_ENDED, False, "OPEN", True, False),
        (CLIP_ENDED, False, "CLOSING", True, False),
        # A close stuck on a slow chain is cut short, so the pass ends
        (CLIP_ENDED, HANGS, "OPEN", True, False),
        # Only the expiry close at EndsAt ends the session of an unconfirmed submit
        (SUBMIT_UNCONFIRMED, None, "OPEN", False, False),
        (SUBMIT_UNCONFIRMED, None, "EXPIRED", False, True),
    ],
)
async def test_session_close_is_stamped_only_once_the_session_is_closed(
    row_values, closed_now, routed_state, close_sent, stamped
):
    row = _row(status="failed", billing_state="voided", **row_values)
    close = AsyncMock(side_effect=_hang) if closed_now == HANGS else AsyncMock(return_value=closed_now)

    with patch.object(jobs, "get_db", FakeDb(row).get_db), \
            patch.object(jobs, "SWEEP_CLOSE_TIMEOUT_SECONDS", 0.05), \
            patch.object(jobs.session_routing_service, "close_session_now", close), \
            patch.object(jobs.session_routing_service, "get_session_state", AsyncMock(return_value=routed_state)):
        await jobs.sweep()

    assert close.await_count == close_sent
    assert (row.session_closed_at is not None) == stamped


async def test_sweep_closes_sessions_only_after_every_refresh():
    """A slow chain delays no clip's status, settlement or void: the pass
    closes the sessions of the clips it ended last."""
    ended = _row(status="in_progress", node_job_id="vj_1")
    rendering = _row(status="in_progress", node_job_id="vj_2", session_id="0x" + "6b" * 32)
    events = []

    async def read(node_job_id):
        events.append(("read", node_job_id))
        return SUCCEEDED if node_job_id == "vj_1" else RUNNING

    async def close(session_id, reason=""):
        events.append(("close", session_id))
        return True

    settle, void = _billing_mocks()
    with patch.object(jobs, "get_db", FakeDb(ended, rendering).get_db), \
            patch("src.services.proxy_router_service.getVideoJob", AsyncMock(side_effect=read)), \
            patch.object(jobs.billing_service, "settle_fixed_hold", settle), \
            patch.object(jobs.billing_service, "void_usage", void), \
            patch.object(jobs.session_routing_service, "close_session_now", AsyncMock(side_effect=close)):
        await jobs.sweep()

    assert events == [("read", "vj_1"), ("read", "vj_2"), ("close", SESSION_ID)]
    assert ended.status == "completed" and ended.session_closed_at is not None


@pytest.mark.parametrize(
    "status, billing_state, charged",
    [
        ("failed", "none", False),
        # A delivered clip is charged, never forgiven
        ("completed", "held", True),
    ],
)
async def test_stranded_hold_of_a_terminal_row_is_finished(status, billing_state, charged):
    row = _row(status=status, billing_state=billing_state, session_closed_at=_utcnow())
    settle, void = _billing_mocks()

    with patch.object(jobs, "get_db", FakeDb(row).get_db), \
            patch.object(jobs.billing_service, "settle_fixed_hold", settle), \
            patch.object(jobs.billing_service, "void_usage", void):
        assert await jobs._release_stranded_hold(row.id)

    assert settle.await_count == charged
    assert void.await_count == (not charged)
    assert row.billing_state == ("charged" if charged else "voided")
