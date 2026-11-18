"""
Video job service: submit a clip, follow it on the node, settle or void its hold.

One video_jobs row per clip, one session per clip (MVP).

Row status:   pending -> queued / in_progress -> completed | failed
Billing:      none -> held -> charged | voided

- submit_video runs POST /videos after the route resolved the model:
  quote -> price -> row (pending) -> fixed hold, then the route answers
  queued. The session open and the node submit run in a detached task; a
  failure there fails the row and voids the hold, and the client reads it
  on GET.
- refresh_job reads the node job and applies the transition; GET and the
  sweeper share it.
- sweep is one pass of the background sweeper (main.video_job_sweep_loop).

A clip's session is closed only once its job is terminal on the node, or
once the node refused it, never on a client disconnect or timeout: a
provider withholds an undelivered clip when the session closes more than
60 s before EndsAt. When a submit's outcome is unknown (submit_interrupted)
the session is left to the routed_sessions expiry close at EndsAt.

video_url is a vendor bearer link: never log it.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError

from src.core.config import settings
from src.core.direct_model_service import direct_model_service
from src.core.logging_config import get_api_logger
from src.crud.credits import VIDEO_JOBS_ENDPOINT as VIDEO_LEDGER_ENDPOINT
from src.db.database import get_db
from src.db.models import (
    CreditLedger,
    LedgerEntryType,
    LedgerStatus,
    SessionState,
    VideoJob,
    VideoJobStatus,
    VideoBillingState,
    VIDEO_JOB_TERMINAL_STATUSES,
)
from src.schemas.billing import FixedHoldRequest, UsageVoidRequest
from src.services import proxy_router_service
from src.services.billing_service import billing_service
from src.services.pricing.types import CLIP_PRICE_QUANTUM
from src.services.session_routing_service import (
    VIDEO_CLOSE_DEADLINE_SECONDS,
    SessionRoutingError,
    session_routing_service,
)
from src.utils.error_sanitizer import sanitize_error_message
from .errors import VideoError, VideoParamError
from .params import VideoRequest, parse_node_time, render_seconds_per_clip_second

logger = get_api_logger()

UNIT_TYPE_VIDEO_SECOND = "video_second"
# Sweeper passes in a row whose node read is a 404 before a job is given up
# as lost. Only the sweeper counts them, so it takes NODE_MISS_LIMIT poll intervals.
NODE_MISS_LIMIT = 3
# Rows per sweeper pass for stuck submits, stranded holds and sessions left
# to the expiry close (each leaves the set once handled, so a limit cannot
# starve the rest)
SWEEP_BATCH_SIZE = 200
# On-chain session closes per sweeper pass, and again as many reclaims of
# video sessions left CLOSING by a closer that died. They run last and
# together, each bounded by SWEEP_CLOSE_TIMEOUT_SECONDS, so a slow chain
# delays no status refresh, settlement or void; a close that does not go
# through moves its row behind the others (updated_at), so a stuck close
# cannot hold a slot pass after pass. A close cut by the timeout goes back to
# OPEN for a retry.
SWEEP_CLOSE_BATCH_SIZE = 5
# close_session_now bounds its own on-chain close and daylock read; this
# backstop also covers the row reads and writes around it, so it never cuts
# a close that deadline lets finish
SWEEP_CLOSE_TIMEOUT_SECONDS = VIDEO_CLOSE_DEADLINE_SECONDS + 30.0
# Shutdown waits this long for detached submits and closes, then cancels the
# rest: a cancelled submit is failed by the sweeper, a cancelled close goes
# back to OPEN for a retry.
SHUTDOWN_DRAIN_SECONDS = 25.0
_SHUTDOWN_CANCEL_SECONDS = 3.0
# A GET reads the node at most this often per video (per process)
REFRESH_MIN_INTERVAL_SECONDS = 3.0
_REFRESH_MARKS_LIMIT = 10_000

# Worst case of one openSessionByBid (_execute_request: 120 s timeout, 3
# tries) and of the EndsAt read-back after the open (getSessionStatus: 2 tries)
_SESSION_OPEN_SECONDS = 3 * 120
_SESSION_STATUS_READ_SECONDS = 2 * 120
# Room for the wallet throttle queue and the row writes between the stages
PENDING_MARGIN_SECONDS = 300

# Gateway error code of a submit whose outcome is unknown: the node may hold
# the clip, so its session is left to the expiry close at EndsAt
SUBMIT_INTERRUPTED = "submit_interrupted"
# POST v1/video/jobs answers after which the node kept nothing, when the node
# itself answered ({"error": "..."}). The same status from a reverse proxy in
# front of the node proves nothing: the node may have taken the clip.
_NODE_REFUSED_STATUSES = frozenset({400, 404, 422, 502, 503})
_CLOSED_SESSION_STATES = frozenset({SessionState.CLOSED.value, SessionState.EXPIRED.value})

# Quote entry errors that mean the request itself is wrong (node videojob errors)
_REQUEST_ERROR_MARKERS = (
    "invalid video job request",
    "video job quote exceeds",
)
_NODE_TERMINAL_FAILURES = frozenset({"failed", "expired", "cancelled"})
_ERROR_CODE_LIMIT = 64
_ERROR_MESSAGE_LIMIT = 1000

BILLING_SETTLE = "settle"
BILLING_VOID = "void"

VIDEO_ID_RE = re.compile(r"video_[0-9a-f]{32}")

# Strong references to detached tasks (submits, background closes)
_background_tasks: Set[asyncio.Task] = set()
# video id -> monotonic time of this process's last node read
_last_node_reads: "OrderedDict[str, float]" = OrderedDict()


def new_video_id() -> str:
    return "video_" + uuid.uuid4().hex


def is_video_id(value: Optional[str]) -> bool:
    return isinstance(value, str) and VIDEO_ID_RE.fullmatch(value) is not None


def pending_timeout_seconds() -> int:
    """
    How long a pending row may go without progress before the sweeper fails it.

    Counted from updated_at, which each submit stage bumps as it starts (hold
    recorded, session recorded). The limit exceeds the session open walk and
    the node submit together, plus a margin, so a submit still in flight is
    never given up.
    """
    open_walk = (
        settings.SESSION_OPEN_BID_WALK_MAX_ATTEMPTS * _SESSION_OPEN_SECONDS
        + _SESSION_STATUS_READ_SECONDS
    )
    return int(open_walk + settings.PROXY_ROUTER_VIDEO_SUBMIT_TIMEOUT + PENDING_MARGIN_SECONDS)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _error_text(message: Optional[str]) -> str:
    return sanitize_error_message(str(message or ""))[:_ERROR_MESSAGE_LIMIT]


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _mark_node_read(video_id: str) -> bool:
    """Record a node read of video_id now. False: this process read it less
    than REFRESH_MIN_INTERVAL_SECONDS ago."""
    now = time.monotonic()
    last = _last_node_reads.get(video_id)
    if last is not None and now - last < REFRESH_MIN_INTERVAL_SECONDS:
        return False
    _last_node_reads[video_id] = now
    _last_node_reads.move_to_end(video_id)
    while len(_last_node_reads) > _REFRESH_MARKS_LIMIT:
        _last_node_reads.popitem(last=False)
    return True


def _spawn(coro, *, name: str) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_reap)
    return task


async def drain_background_tasks(timeout: float = SHUTDOWN_DRAIN_SECONDS) -> int:
    """
    Shutdown: wait up to timeout for the detached submits and session closes,
    then cancel what is still running and give the cancelled ones a moment to
    write their outcome. Call it before the HTTP client and the DB engine go.

    A cancelled submit leaves its row pending for the sweeper (submit
    interrupted, hold voided, session left to the expiry close). A cancelled
    close puts its session back to OPEN for a retry.

    Returns:
        The number of tasks cancelled.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    # A task can spawn another (a GET's background close): wait for those too
    while True:
        left = [task for task in _background_tasks if not task.done()]
        remaining = deadline - loop.time()
        if not left or remaining <= 0:
            break
        await asyncio.wait(left, timeout=remaining)

    for task in left:
        task.cancel()
    if left:
        logger.warning("Video background tasks cancelled at shutdown",
                       tasks=len(left),
                       names=[task.get_name() for task in left][:20],
                       event_type="video_background_tasks_cancelled")
        await asyncio.wait(left, timeout=_SHUTDOWN_CANCEL_SECONDS)
    return len(left)


def _reap(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None and not isinstance(exc, VideoError):
        logger.error("Video background task failed",
                     task=task.get_name(),
                     error=str(exc),
                     event_type="video_background_task_error")


class _SubmitInterrupted(Exception):
    """The row stopped being pending mid-submit: the sweeper gave up on it."""

    def __init__(self, session_id: Optional[str] = None):
        super().__init__("video row is no longer pending")
        self.session_id = session_id


class VideoJobCommitError(Exception):
    """A video row update did not commit. It carries no SQL text: the failed
    statement can hold video_url as a bound parameter."""

    def __init__(self, video_id: str):
        super().__init__(f"video job {video_id} update was not committed")
        self.video_id = video_id


# === Quote selection (pure) ===

def _resolved_key(resolved: Dict[str, Any]) -> Tuple[Any, Any, Any, Any]:
    return (
        resolved.get("duration_seconds"),
        resolved.get("resolution"),
        resolved.get("generate_audio"),
        resolved.get("has_image"),
    )


def select_quote_candidates(
    entries: Iterable[Any],
    healthy_bid_ids: Set[str],
) -> List[Dict[str, Any]]:
    """
    Quote entries that can take the clip, cheapest session first.

    Kept: no error, session_seconds > 0, session_cost_wei set, a resolved
    block, and a bid the catalog reports healthy. Then only the entries whose
    resolved (duration, resolution, audio, image) equals the cheapest one's,
    so the price held matches whichever bid opens. Equal costs keep the
    node's rating order.
    """
    usable: List[Tuple[int, Dict[str, Any]]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        bid_id = str(entry.get("bid_id") or "").strip().lower()
        if not bid_id or bid_id not in healthy_bid_ids:
            continue
        if not isinstance(entry.get("resolved"), dict):
            continue
        try:
            session_seconds = int(entry.get("session_seconds") or 0)
            cost = int(str(entry.get("session_cost_wei")).strip())
        except (TypeError, ValueError):
            continue
        if session_seconds <= 0 or cost < 0:
            continue
        usable.append((cost, entry))

    if not usable:
        return []
    usable.sort(key=lambda pair: pair[0])
    cheapest = _resolved_key(usable[0][1]["resolved"])
    return [entry for _, entry in usable if _resolved_key(entry["resolved"]) == cheapest]


def request_error_message(entries: List[Any]) -> Optional[str]:
    """The first entry error when every entry refused the request itself, else None."""
    errors = [str(e.get("error") or "") if isinstance(e, dict) else "" for e in entries]
    if not errors or not all(errors):
        return None
    if all(any(marker in err for marker in _REQUEST_ERROR_MARKERS) for err in errors):
        return errors[0]
    return None


# === Refresh transition (pure) ===

@dataclass(frozen=True)
class RefreshPlan:
    """What one node read changes on a row. All None: nothing to write."""

    status: Optional[str] = None
    billing: Optional[str] = None  # BILLING_SETTLE | BILLING_VOID
    failure_code: Optional[str] = None  # ledger failure_code of a void
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    video_url: Optional[str] = field(default=None, repr=False)  # bearer link: never logged
    expires_at: Optional[datetime] = None
    node_misses: Optional[int] = None

    @property
    def changes_row(self) -> bool:
        return self.status is not None or self.node_misses is not None


def _failed(failure_code: str, error_code: str, message: str, misses: Optional[int]) -> RefreshPlan:
    return RefreshPlan(
        status=VideoJobStatus.failed.value,
        billing=BILLING_VOID,
        failure_code=failure_code,
        error_code=error_code,
        error_message=message,
        node_misses=misses,
    )


def plan_refresh(
    *,
    status: str,
    node_misses: int,
    created_at: Optional[datetime],
    node_job: Optional[Dict[str, Any]],
    node_missing: bool,
    now: datetime,
    max_age_seconds: int,
) -> RefreshPlan:
    """
    The transition of a non-terminal row after one node read.

    node_job is the node Job, or None when the read failed. node_missing is
    True when the node answered 404 and the miss counts. A node terminal
    state wins over the age limit: a clip that finished late is still
    delivered and charged, unless its link has already expired by now (the
    user could never fetch it): that clip fails and its hold is voided.
    """
    misses: Optional[int] = None
    running = False

    if node_missing:
        misses = (node_misses or 0) + 1
        if misses >= NODE_MISS_LIMIT:
            return _failed("node_lost", "node_lost", "The node lost track of this video job", misses)
    elif node_job is not None:
        if node_misses:
            misses = 0
        node_status = str(node_job.get("status") or "").strip().lower()
        if node_status == "succeeded":
            url = node_job.get("video_url")
            if isinstance(url, str) and url.startswith("https://"):
                expires_at = parse_node_time(node_job.get("expires_at"))
                if expires_at is not None and expires_at <= now:
                    return _failed("job_link_expired", "video_expired",
                                   "The video link expired before it could be delivered", misses)
                return RefreshPlan(
                    status=VideoJobStatus.completed.value,
                    billing=BILLING_SETTLE,
                    video_url=url,
                    expires_at=expires_at,
                    node_misses=misses,
                )
            return _failed("job_succeeded_without_url", "node_error",
                           "The video finished without a usable download link", misses)
        if node_status in _NODE_TERMINAL_FAILURES:
            error = node_job.get("error") if isinstance(node_job.get("error"), dict) else {}
            return _failed(
                f"job_{node_status}",
                str(error.get("code") or node_status),
                str(error.get("message") or f"The video job {node_status}"),
                misses,
            )
        running = node_status == "running"

    if created_at is not None and now - created_at > timedelta(seconds=max_age_seconds):
        return _failed("timeout", "timeout", "The video did not finish in time", misses)

    if running and status == VideoJobStatus.queued.value:
        return RefreshPlan(status=VideoJobStatus.in_progress.value, node_misses=misses)
    return RefreshPlan(node_misses=misses)


# === Row access ===

async def _lock_job(db, video_id: str, *, skip_locked: bool = False) -> Optional[VideoJob]:
    result = await db.execute(
        select(VideoJob).where(VideoJob.id == video_id).with_for_update(skip_locked=skip_locked)
    )
    return result.scalar_one_or_none()


async def load_job(video_id: str) -> Optional[VideoJob]:
    async with get_db() as db:
        result = await db.execute(select(VideoJob).where(VideoJob.id == video_id))
        return result.scalar_one_or_none()


# Past render rates per (model_name, resolution), for the progress estimate.
RENDER_RATE_CACHE_SECONDS = 600.0
RENDER_RATE_SAMPLES = 20
_RENDER_RATE_CACHE_MAX = 256
_render_rates: "OrderedDict[Tuple[str, str], Tuple[Optional[float], float]]" = OrderedDict()


async def render_rates_for(jobs: Iterable[VideoJob]) -> Dict[Tuple[str, str], Optional[float]]:
    """
    Median render seconds per clip second of the last finished clips, per
    (model_name, resolution) of the running jobs given. None where there is
    too little history; a failed read gives None too, never an error.
    """
    wanted = {(job.model_name, job.resolution) for job in jobs
              if job.status == VideoJobStatus.in_progress.value}
    rates: Dict[Tuple[str, str], Optional[float]] = {}
    now = time.monotonic()
    for key in wanted:
        cached = _render_rates.get(key)
        if cached is not None and now - cached[1] < RENDER_RATE_CACHE_SECONDS:
            rates[key] = cached[0]
            continue
        try:
            async with get_db() as db:
                result = await db.execute(
                    select(VideoJob.created_at, VideoJob.completed_at, VideoJob.duration_seconds)
                    .where(
                        VideoJob.model_name == key[0],
                        VideoJob.resolution == key[1],
                        VideoJob.status == VideoJobStatus.completed.value,
                        VideoJob.completed_at.is_not(None),
                    )
                    .order_by(VideoJob.completed_at.desc())
                    .limit(RENDER_RATE_SAMPLES)
                )
                samples = [
                    (completed - created).total_seconds() / duration
                    for created, completed, duration in result.all()
                    if created and completed and duration
                ]
        except Exception as e:
            logger.warning("Error reading video render times",
                           error_class=type(e).__name__,
                           event_type="video_render_rate_error")
            rates[key] = None
            continue
        rate = render_seconds_per_clip_second(samples)
        _render_rates[key] = (rate, now)
        _render_rates.move_to_end(key)
        while len(_render_rates) > _RENDER_RATE_CACHE_MAX:
            _render_rates.popitem(last=False)
        rates[key] = rate
    return rates


async def get_job_for_user(video_id: str, user_id: int) -> Optional[VideoJob]:
    """The row when it belongs to user_id; None for a missing or foreign id."""
    if not is_video_id(video_id):
        return None
    async with get_db() as db:
        result = await db.execute(
            select(VideoJob).where(VideoJob.id == video_id, VideoJob.user_id == user_id)
        )
        return result.scalar_one_or_none()


async def get_job_by_idempotency_key(user_id: int, idempotency_key: str) -> Optional[VideoJob]:
    """The caller's row created with this Idempotency-Key, if any."""
    async with get_db() as db:
        result = await db.execute(
            select(VideoJob).where(
                VideoJob.user_id == user_id,
                VideoJob.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()


async def list_jobs_for_user(
    user_id: int,
    *,
    after: Optional[str] = None,
    limit: int = 20,
    order: str = "desc",
) -> Tuple[List[VideoJob], bool]:
    """
    The caller's rows by created_at (id breaks ties), after the ``after`` cursor.

    Returns:
        (rows, has_more)

    Raises:
        VideoParamError: ``after`` is not one of the caller's videos
    """
    descending = order != "asc"
    async with get_db() as db:
        stmt = select(VideoJob).where(VideoJob.user_id == user_id)
        if after:
            cursor = None
            if is_video_id(after):
                cursor = (await db.execute(
                    select(VideoJob.created_at, VideoJob.id)
                    .where(VideoJob.id == after, VideoJob.user_id == user_id)
                )).first()
            if cursor is None:
                raise VideoParamError("after is not one of your videos", "after")
            key = tuple_(VideoJob.created_at, VideoJob.id)
            mark = tuple_(cursor.created_at, cursor.id)
            stmt = stmt.where(key < mark if descending else key > mark)
        if descending:
            stmt = stmt.order_by(VideoJob.created_at.desc(), VideoJob.id.desc())
        else:
            stmt = stmt.order_by(VideoJob.created_at.asc(), VideoJob.id.asc())
        rows = list((await db.execute(stmt.limit(limit + 1))).scalars().all())
    return rows[:limit], len(rows) > limit


async def _insert_job(job: VideoJob) -> Optional[VideoJob]:
    """Insert a new row. Returns the row that already holds its idempotency
    key when a concurrent duplicate won the insert, else None."""
    try:
        async with get_db() as db:
            db.add(job)
            await db.commit()
    except IntegrityError:
        winner = None
        if job.idempotency_key:
            winner = await get_job_by_idempotency_key(job.user_id, job.idempotency_key)
        if winner is None:
            raise
        return winner
    return None


async def _update_pending(video_id: str, **values: Any) -> bool:
    """Update the row only while it is pending. False: the sweeper failed it meanwhile."""
    values.setdefault("updated_at", _utcnow())
    async with get_db() as db:
        result = await db.execute(
            update(VideoJob)
            .where(VideoJob.id == video_id, VideoJob.status == VideoJobStatus.pending.value)
            .values(**values)
        )
        await db.commit()
    return result.rowcount == 1


# === Billing on a locked row (caller commits) ===

async def _void_hold(db, job: VideoJob, failure_code: str, reason: str) -> None:
    """Void the clip's hold, if there is one, in the caller's transaction."""
    if job.billing_state in (VideoBillingState.charged.value, VideoBillingState.voided.value):
        return
    response = await billing_service.void_usage(
        db,
        job.user_id,
        UsageVoidRequest(
            ledger_entry_id=job.ledger_entry_id,
            failure_code=failure_code,
            failure_reason=reason[:500],
        ),
        commit=False,
    )
    if response.voided:
        job.billing_state = VideoBillingState.voided.value
    elif response.error != "Hold not found":
        logger.error("Video hold could not be voided",
                     video_id=job.id,
                     user_id=job.user_id,
                     ledger_entry_id=str(job.ledger_entry_id),
                     error=response.error,
                     event_type="video_void_failed")


async def _settle_hold(db, job: VideoJob) -> None:
    """Charge exactly the held clip price in the caller's transaction."""
    response = await billing_service.settle_fixed_hold(
        db, job.user_id, job.ledger_entry_id, commit=False
    )
    if response.success:
        job.billing_state = VideoBillingState.charged.value
        return
    # The clip exists either way: it is delivered, and ops sees the miss
    logger.error("Completed video could not be charged",
                 video_id=job.id,
                 user_id=job.user_id,
                 ledger_entry_id=str(job.ledger_entry_id),
                 error=response.error,
                 event_type="video_settle_failed")
    if response.error == "hold_voided":
        job.billing_state = VideoBillingState.voided.value


async def _fail_pending_job(
    video_id: str,
    *,
    error_code: str,
    error_message: str,
    failure_code: Optional[str] = None,
    void: bool = True,
    release_idempotency_key: bool = False,
    stale_before: Optional[datetime] = None,
) -> Optional[VideoJob]:
    """Fail a row that is still pending, voiding its hold in the same commit.

    release_idempotency_key: the client got an error, not this video, so a
    retry with the same key starts afresh. stale_before (the sweeper): only
    a row whose submit made no progress since then; a stage that started
    meanwhile bumped updated_at. A row that is no longer pending, or no
    longer stale, is returned unchanged.
    """
    async with get_db() as db:
        job = await _lock_job(db, video_id)
        if job is None or job.status != VideoJobStatus.pending.value:
            return job
        if stale_before is not None and job.updated_at is not None and job.updated_at >= stale_before:
            return job
        if void:
            await _void_hold(db, job, failure_code or error_code, error_message)
        job.status = VideoJobStatus.failed.value
        job.error_code = error_code[:_ERROR_CODE_LIMIT]
        job.error_message = _error_text(error_message)
        if release_idempotency_key:
            job.idempotency_key = None
        job.updated_at = _utcnow()
        await db.commit()
    return job


async def _void_hold_now(job: VideoJob, failure_code: str, reason: str) -> None:
    """Void a hold outside the row lock (the row is already terminal)."""
    try:
        async with get_db() as db:
            await billing_service.void_usage(
                db,
                job.user_id,
                UsageVoidRequest(
                    ledger_entry_id=job.ledger_entry_id,
                    failure_code=failure_code,
                    failure_reason=reason[:500],
                ),
            )
    except Exception as e:
        logger.error("Error voiding video hold",
                     video_id=job.id,
                     error=str(e),
                     event_type="video_void_error")


# === Sessions ===

def _session_closable(job: Any) -> bool:
    """
    Whether the gateway may close the clip's session now.

    Not after a submit whose outcome is unknown (submit_interrupted with no
    node job id): the provider may have taken the clip, and a close more
    than 60 s before EndsAt withholds it. The routed_sessions expiry close
    ends that session at EndsAt + buffer.
    """
    return bool(job.node_job_id) or job.error_code != SUBMIT_INTERRUPTED


def _session_closable_clause():
    """_session_closable as a video_jobs SQL condition (a NULL error_code is closable)."""
    return or_(
        VideoJob.node_job_id.isnot(None),
        VideoJob.error_code.is_(None),
        VideoJob.error_code != SUBMIT_INTERRUPTED,
    )


async def _close_job_session(
    video_id: str,
    session_id: str,
    reason: str,
    *,
    close_now: bool = True,
) -> bool:
    """
    Close a clip's session and stamp session_closed_at once it is closed.

    Stamped when close_session_now closed it, or when the routed_sessions row
    is already CLOSED or EXPIRED. Any other outcome (a failed close left OPEN
    for a retry, a close still in flight, an error) leaves session_closed_at
    empty, so the sweeper retries; close_session_now claims a close left
    CLOSING too long again, so a dead closer does not stall it. close_now=False
    only waits for the expiry close: nothing is sent on chain.

    Returns:
        True when session_closed_at was stamped.
    """
    closed = False
    try:
        if close_now:
            closed = await session_routing_service.close_session_now(session_id, reason=reason)
        if not closed:
            closed = await session_routing_service.get_session_state(session_id) in _CLOSED_SESSION_STATES
    except Exception as e:
        logger.warning("Video session close failed; the sweeper retries it",
                       video_id=video_id,
                       session_id=session_id,
                       error=str(e),
                       event_type="video_session_close_error")
    if not closed:
        if close_now:
            await _requeue_close(video_id)
        return False

    now = _utcnow()
    async with get_db() as db:
        await db.execute(
            update(VideoJob)
            .where(VideoJob.id == video_id, VideoJob.session_closed_at.is_(None))
            .values(session_closed_at=now, updated_at=now)
        )
        await db.commit()
    return True


async def _requeue_close(video_id: str) -> None:
    """Move a row whose close did not go through behind the other close
    retries (they are taken oldest updated_at first)."""
    try:
        now = _utcnow()
        async with get_db() as db:
            await db.execute(
                update(VideoJob)
                .where(
                    VideoJob.id == video_id,
                    VideoJob.session_closed_at.is_(None),
                    VideoJob.status.in_(list(VIDEO_JOB_TERMINAL_STATUSES)),
                )
                .values(updated_at=now)
            )
            await db.commit()
    except Exception as e:
        logger.warning("Could not requeue a video session close",
                       video_id=video_id,
                       error_class=type(e).__name__,
                       event_type="video_session_close_requeue_error")


async def _ensure_session_closed(job: VideoJob, *, background: bool) -> None:
    if not job.session_id or job.session_closed_at is not None:
        return
    close = _close_job_session(
        job.id, job.session_id, f"video_{job.status}", close_now=_session_closable(job)
    )
    if background:
        _spawn(close, name=f"video-close-{job.id}")
    else:
        await close


async def _close_unrecorded_session(session_id: str) -> None:
    """Close a session that never made it onto its row (no job was submitted on it)."""
    try:
        await session_routing_service.close_session_now(session_id, reason="video_submit_interrupted")
    except Exception as e:
        logger.warning("Could not close the session of an interrupted video submit",
                       session_id=session_id,
                       error=str(e),
                       event_type="video_session_close_error")


# === Submit (POST /videos) ===

def _quote_error(e: proxy_router_service.ProxyRouterServiceError, model_name: str) -> VideoError:
    if e.status_code in (400, 422):
        return VideoError(400, "invalid_request", e.message)
    if e.status_code in (404, 503):
        return VideoError(503, "model_unavailable",
                          f"Video generation is not available for {model_name} right now",
                          code="model_unavailable")
    return VideoError(502, "proxy_error", "Could not get a video quote from the node")


async def _quote_candidates(
    *,
    model_id: str,
    model_name: str,
    node_request: Dict[str, Any],
    video_id: str,
    video_logger,
) -> List[Dict[str, Any]]:
    try:
        quote = await proxy_router_service.videoQuote(
            model_id=model_id, request=node_request, request_id=video_id
        )
    except proxy_router_service.ProxyRouterServiceError as e:
        video_logger.warning("Video quote failed",
                             status_code=e.status_code,
                             error=str(e),
                             event_type="video_quote_failed")
        raise _quote_error(e, model_name) from e

    entries = quote.get("data") if isinstance(quote.get("data"), list) else []
    healthy = await direct_model_service.get_healthy_bid_ids_for_model(model_id)
    candidates = select_quote_candidates(entries, healthy)
    if candidates:
        video_logger.info("Video quote candidates selected",
                          quoted=len(entries),
                          candidates=len(candidates),
                          event_type="video_quote_candidates")
        return candidates

    refused = request_error_message(entries)
    video_logger.warning("No quoted bid can take the video",
                         quoted=len(entries),
                         healthy_bids=len(healthy),
                         request_refused=refused is not None,
                         event_type="video_quote_no_candidates")
    if refused:
        raise VideoError(400, "invalid_request", refused)
    raise VideoError(503, "model_unavailable",
                     f"No provider can take a {model_name} video right now",
                     code="model_unavailable")


async def _clip_price(model_id: str, model_name: str, resolved: Dict[str, Any]) -> Decimal:
    """The clip's USD price from clip_prices. Fails closed: unpriced is a 503."""
    pricing = await billing_service.pricing.get_clip_pricing(model_name=model_name, model_id=model_id)
    price = None
    if pricing is not None:
        price = pricing.price_for(
            resolved.get("duration_seconds"),
            resolved.get("resolution"),
            bool(resolved.get("generate_audio")),
        )
    if price is None:
        raise VideoError(503, "model_unavailable", f"{model_name} is not priced for video")
    return price


def _usage_metadata(quoted: Dict[str, Any]) -> Dict[str, Any]:
    """Ledger usage details: the resolved clip (no prompt) and the cheapest quote."""
    resolved = quoted.get("resolved") or {}
    return {
        "resolved": {
            key: resolved.get(key)
            for key in ("duration_seconds", "resolution", "aspect_ratio", "generate_audio", "has_image")
        },
        "bid_id": quoted.get("bid_id"),
        "provider": quoted.get("provider"),
        "quote_seconds": quoted.get("quote_seconds"),
        "session_seconds": quoted.get("session_seconds"),
    }


async def _hold_clip(job: VideoJob, quoted: Dict[str, Any], video_logger) -> None:
    unit_price = (job.price_usd / Decimal(job.duration_seconds)).quantize(
        CLIP_PRICE_QUANTUM, rounding=ROUND_HALF_UP
    )
    hold_request = FixedHoldRequest(
        ledger_entry_id=job.ledger_entry_id,
        request_id=job.id,
        amount=job.price_usd,
        unit_type=UNIT_TYPE_VIDEO_SECOND,
        units=Decimal(job.duration_seconds),
        unit_price=unit_price,
        api_key_id=job.api_key_id,
        model_name=job.model_name,
        model_id=job.model_id,
        endpoint=VIDEO_LEDGER_ENDPOINT,
        usage_metadata=_usage_metadata(quoted),
    )
    try:
        async with get_db() as db:
            hold = await billing_service.create_fixed_hold(db, job.user_id, hold_request)
    except Exception as e:
        video_logger.error("Error creating video hold",
                           error=str(e),
                           event_type="video_hold_error",
                           exc_info=True)
        await _fail_pending_job(job.id, error_code="billing_error",
                                error_message="Could not hold credits for the video",
                                failure_code="hold_error", release_idempotency_key=True)
        raise VideoError(500, "billing_error", "Billing error: could not hold credits for the video") from e

    if not hold.success:
        if hold.error == "insufficient_balance":
            await _fail_pending_job(job.id, error_code="insufficient_balance",
                                    error_message="Insufficient credits", void=False,
                                    release_idempotency_key=True)
            video_logger.info("Insufficient balance for video",
                              price_usd=str(job.price_usd),
                              event_type="video_insufficient_balance")
            # Same body as chat's InsufficientBalanceError
            raise VideoError(402, "insufficient_balance", "Insufficient credits", details={
                "available": str(hold.available_balance),
                "estimated_cost": str(hold.estimated_cost),
            })
        await _fail_pending_job(job.id, error_code="billing_error",
                                error_message=f"Billing error: {hold.error}", void=False,
                                release_idempotency_key=True)
        raise VideoError(500, "billing_error", f"Billing error: {hold.error}")

    if not await _update_pending(job.id, billing_state=VideoBillingState.held.value):
        raise _SubmitInterrupted()
    video_logger.info("Video hold created",
                      price_usd=str(job.price_usd),
                      event_type="video_hold_created")


async def _open_clip_session(
    job: VideoJob,
    candidates: List[Dict[str, Any]],
    video_logger,
) -> Optional[str]:
    """Open the clip's session and record it on the row. None: the open
    failed, and the row is failed with its hold voided."""
    try:
        session_id, chosen = await session_routing_service.open_session_for_quote(
            model_id=job.model_id,
            model_name=job.model_name,
            candidates=candidates,
            user_id=job.user_id,
        )
    except Exception as e:
        message = (e.message if isinstance(e, SessionRoutingError)
                   else "Could not open a session for the video")
        video_logger.warning("Video session open failed",
                             error=str(e),
                             error_class=e.__class__.__name__,
                             event_type="video_session_open_failed")
        await _fail_pending_job(job.id, error_code="session_open_failed", error_message=message,
                                failure_code="session_open_failed")
        return None

    recorded = await _update_pending(
        job.id,
        session_id=session_id,
        bid_id=str(chosen.get("bid_id") or "") or None,
        provider=chosen.get("provider") or None,
        quote_seconds=_int_or_none(chosen.get("quote_seconds")),
        session_seconds=_int_or_none(chosen.get("session_seconds")),
    )
    if not recorded:
        raise _SubmitInterrupted(session_id)
    video_logger.info("Video session opened",
                      session_id=session_id,
                      bid_id=chosen.get("bid_id"),
                      provider=chosen.get("provider"),
                      session_seconds=chosen.get("session_seconds"),
                      event_type="video_session_opened")
    return session_id


async def _submit_node_job(
    job: VideoJob,
    session_id: str,
    node_request: Dict[str, Any],
    resolved: Dict[str, Any],
    video_logger,
) -> None:
    # Pin what was priced, so the render cannot drift from the hold
    submit_request = {
        **node_request,
        "duration_seconds": resolved.get("duration_seconds"),
        "resolution": resolved.get("resolution"),
        "generate_audio": resolved.get("generate_audio"),
    }
    if resolved.get("aspect_ratio"):
        submit_request["aspect_ratio"] = resolved["aspect_ratio"]

    try:
        node_job = await proxy_router_service.createVideoJob(
            session_id=session_id,
            request=submit_request,
            request_id=job.id,
            timeout=settings.PROXY_ROUTER_VIDEO_SUBMIT_TIMEOUT,
        )
    except proxy_router_service.ProxyRouterServiceError as e:
        video_logger.warning("Video job submit failed",
                             session_id=session_id,
                             status_code=e.status_code,
                             node_answered=e.node_error_body,
                             error=str(e),
                             event_type="video_submit_failed")
        if e.status_code in _NODE_REFUSED_STATUSES and e.node_error_body:
            await _fail_pending_job(
                job.id,
                error_code="node_error",
                error_message=(e.message if e.status_code in (400, 422)
                               else f"The node could not start the video job: {e.message}"),
                failure_code=f"node_refused_{e.status_code}",
            )
            # The node kept nothing: the session is empty, close it now
            await _close_job_session(job.id, session_id, "video_submit_failed")
        else:
            # A timeout, an unexpected answer, or an error page from a proxy
            # in front of the node: the node may hold the clip, so its
            # session is left to the expiry close at EndsAt
            await _fail_pending_job(
                job.id,
                error_code=SUBMIT_INTERRUPTED,
                error_message="The node did not confirm the video job; you were not charged",
                failure_code="node_error",
            )
        return

    node_job_id = str(node_job["id"])
    status = (
        VideoJobStatus.in_progress.value
        if str(node_job.get("status") or "").lower() == "running"
        else VideoJobStatus.queued.value
    )
    if not await _update_pending(job.id, node_job_id=node_job_id, status=status):
        # The sweeper failed the row and voided the hold; its session is left
        # to the expiry close, and the caller sees a failed video.
        video_logger.error("Video job reached the node after its row was given up",
                           node_job_id=node_job_id,
                           event_type="video_submit_late")
        return
    video_logger.info("Video job submitted",
                      node_job_id=node_job_id,
                      status=status,
                      vendor_confirmed=bool(node_job.get("vendor_task_id")),
                      event_type="video_job_submitted")


async def _open_and_submit(
    job: VideoJob,
    candidates: List[Dict[str, Any]],
    node_request: Dict[str, Any],
    resolved: Dict[str, Any],
    video_logger,
) -> None:
    """The detached half of POST /videos: open the clip's session, then submit
    the node job. Every failure fails the row and voids the hold."""
    try:
        session_id = await _open_clip_session(job, candidates, video_logger)
        if session_id is not None:
            await _submit_node_job(job, session_id, node_request, resolved, video_logger)
    except _SubmitInterrupted as e:
        video_logger.error("Video row stopped being pending mid-submit; submission abandoned",
                           event_type="video_submit_interrupted")
        await _void_hold_now(job, SUBMIT_INTERRUPTED, "Submission abandoned after the row was given up")
        if e.session_id:
            await _close_unrecorded_session(e.session_id)
    except Exception as e:
        # A row write failed mid-submit. The node may already hold the clip,
        # so any session is left to its expiry close; the sweeper fails the
        # row if this cannot.
        video_logger.error("Video submit failed unexpectedly",
                           error_class=type(e).__name__,
                           event_type="video_submit_error")
        try:
            await _fail_pending_job(job.id, error_code=SUBMIT_INTERRUPTED,
                                    error_message="The video submission was interrupted; you were not charged")
        except Exception as fail_error:
            video_logger.error("Could not fail the interrupted video row",
                               error_class=type(fail_error).__name__,
                               event_type="video_submit_error")


async def submit_video(
    *,
    user_id: int,
    api_key_id: Optional[int],
    model_id: str,
    model_name: str,
    request: VideoRequest,
    request_id: str,
    idempotency_key: Optional[str] = None,
) -> VideoJob:
    """
    POST /videos after the route resolved the model: quote, price, row and
    fixed hold. The session open and the node submit then run in a detached
    task, so a client disconnect cannot stop them half way.

    Returns:
        The pending row (public status queued). When a concurrent request
        already created a row with idempotency_key, that row: no hold is placed.

    Raises:
        VideoError: 400 invalid_request, 402 insufficient_balance,
            500 billing_error, 502 proxy_error, 503 model_unavailable.
            Once the row exists it is failed first.
    """
    video_id = new_video_id()
    video_logger = logger.bind(video_id=video_id, user_id=user_id, model_id=model_id,
                               request_id=request_id)
    node_request = request.node_request()

    candidates = await _quote_candidates(
        model_id=model_id,
        model_name=model_name,
        node_request=node_request,
        video_id=video_id,
        video_logger=video_logger,
    )
    resolved = candidates[0]["resolved"]
    price = await _clip_price(model_id, model_name, resolved)

    # The row comes first: from here on the sweeper owns anything left pending
    now = _utcnow()
    job = VideoJob(
        id=video_id,
        user_id=user_id,
        api_key_id=api_key_id,
        idempotency_key=idempotency_key,
        model_name=model_name,
        model_id=model_id,
        prompt=request.prompt,
        duration_seconds=int(resolved["duration_seconds"]),
        resolution=str(resolved.get("resolution") or "")[:16],
        aspect_ratio=(str(resolved.get("aspect_ratio") or "")[:16] or None),
        generate_audio=bool(resolved.get("generate_audio")),
        has_image=bool(resolved.get("has_image")),
        status=VideoJobStatus.pending.value,
        price_usd=price,
        ledger_entry_id=uuid.uuid4(),
        billing_state=VideoBillingState.none.value,
        node_misses=0,
        created_at=now,
        updated_at=now,
    )
    winner = await _insert_job(job)
    if winner is not None:
        video_logger.info("Concurrent duplicate of an idempotent video request",
                          existing_video_id=winner.id,
                          event_type="video_idempotent_replay")
        return winner
    video_logger.info("Video job row created",
                      price_usd=str(price),
                      duration_seconds=job.duration_seconds,
                      resolution=job.resolution,
                      generate_audio=job.generate_audio,
                      has_image=job.has_image,
                      event_type="video_job_row_created")

    try:
        await _hold_clip(job, candidates[0], video_logger)
    except _SubmitInterrupted as e:
        video_logger.error("Video row stopped being pending before its hold was recorded",
                           event_type="video_submit_interrupted")
        await _void_hold_now(job, SUBMIT_INTERRUPTED, "Submission abandoned after the row was given up")
        raise VideoError(500, "gateway_error",
                         "The video submission was interrupted; you were not charged") from e

    _spawn(_open_and_submit(job, candidates, node_request, resolved, video_logger),
           name=f"video-submit-{video_id}")
    return job


# === Refresh (GET and the sweeper) ===

async def _apply_plan(db, job: VideoJob, plan: RefreshPlan, now: datetime) -> None:
    # Billing first: its queries autoflush the row, which must not carry
    # video_url yet (a flush error would print it as a parameter)
    if plan.billing == BILLING_SETTLE:
        await _settle_hold(db, job)
    elif plan.billing == BILLING_VOID:
        await _void_hold(db, job, plan.failure_code or "job_failed", plan.error_message or "")

    if plan.status is not None:
        job.status = plan.status
    if plan.status == VideoJobStatus.completed.value:
        job.video_url = plan.video_url
        job.expires_at = plan.expires_at
        job.completed_at = now
    elif plan.status == VideoJobStatus.failed.value:
        job.error_code = (plan.error_code or "")[:_ERROR_CODE_LIMIT] or None
        job.error_message = _error_text(plan.error_message)
    if plan.node_misses is not None:
        job.node_misses = plan.node_misses
    job.updated_at = now


async def refresh_job(
    video_id: str,
    *,
    client_read: bool = False,
    close_session: bool = True,
) -> Optional[VideoJob]:
    """
    Read the node job and apply the transition (shared by GET and the sweeper).

    1. Claim the row (FOR UPDATE SKIP LOCKED); a row someone else is applying
       is returned as it is.
    2. Read the node job with no DB connection held. A 404 the node itself
       answered counts a miss; a network error, or a 404 page from a proxy
       in front of the node, changes nothing (the age limit still applies).
    3. Apply under the row lock: settle on success, void on a failure, the
       age limit or a lost job. The billing and the row commit together.
    4. Once terminal, close the clip's session.

    client_read (GET): the node is read at most once per
    REFRESH_MIN_INTERVAL_SECONDS per video in this process, a node 404 does
    not count a miss (only the sweeper counts them, once per pass), and the
    close runs in a background task so a GET does not wait for the chain.
    close_session=False (the sweeper): no close here; the pass closes
    terminal rows last, after every refresh.

    Returns:
        The row after the refresh, or None when it does not exist.

    Raises:
        VideoJobCommitError: the row update did not commit.
    """
    async with get_db() as db:
        job = await _lock_job(db, video_id, skip_locked=True)
    if job is None:
        return await load_job(video_id)
    if job.is_terminal:
        if close_session:
            await _ensure_session_closed(job, background=client_read)
        return job
    if not job.node_job_id:
        return job
    if not _mark_node_read(video_id) and client_read:
        return job

    node_job: Optional[Dict[str, Any]] = None
    node_missing = False
    try:
        node_job = await proxy_router_service.getVideoJob(job.node_job_id)
    except proxy_router_service.ProxyRouterServiceError as e:
        if e.status_code == 404 and e.node_error_body:
            node_missing = not client_read
        else:
            logger.warning("Video job status read failed",
                           video_id=video_id,
                           node_job_id=job.node_job_id,
                           status_code=e.status_code,
                           error=str(e),
                           event_type="video_job_read_failed")

    now = _utcnow()
    async with get_db() as db:
        job = await _lock_job(db, video_id)
        if job is None:
            return None
        if not job.is_terminal:
            plan = plan_refresh(
                status=job.status,
                node_misses=job.node_misses,
                created_at=job.created_at,
                node_job=node_job,
                node_missing=node_missing,
                now=now,
                max_age_seconds=settings.VIDEO_JOB_MAX_AGE_SECONDS,
            )
            if plan.changes_row:
                await _apply_plan(db, job, plan, now)
                try:
                    await db.commit()
                except Exception as e:
                    # The failed UPDATE can carry video_url: never log its text
                    logger.error("Video job update was not committed",
                                 video_id=video_id,
                                 error_class=type(e).__name__,
                                 event_type="video_job_commit_error")
                    raise VideoJobCommitError(video_id) from None
                if plan.status is not None:
                    logger.info("Video job updated",
                                video_id=video_id,
                                user_id=job.user_id,
                                status=job.status,
                                billing_state=job.billing_state,
                                error_code=job.error_code,
                                event_type="video_job_transition")

    if job.is_terminal and close_session:
        await _ensure_session_closed(job, background=client_read)
    return job


# === Sweeper pass ===

async def _interrupt_pending(video_id: str) -> Optional[VideoJob]:
    """
    Fail a row whose submit stopped making progress, and void its hold.

    Staleness is checked again under the row lock, against the clock now: a
    stage that finished during a long sweeper pass bumped updated_at, and
    that row is left to its submit.

    Its session, if one was opened, is not closed: the node may have taken
    the clip. The routed_sessions expiry close ends it at EndsAt.
    """
    return await _fail_pending_job(
        video_id,
        error_code=SUBMIT_INTERRUPTED,
        error_message="The video submission was interrupted; you were not charged",
        failure_code=SUBMIT_INTERRUPTED,
        stale_before=_utcnow() - timedelta(seconds=pending_timeout_seconds()),
    )


async def _release_stranded_hold(video_id: str) -> bool:
    """
    Finish the billing of a terminal row whose hold is still pending: charge
    a completed clip, void a failed one. True when the hold is now final.
    """
    async with get_db() as db:
        job = await _lock_job(db, video_id)
        if job is None or not job.is_terminal:
            return False
        if job.billing_state in (VideoBillingState.charged.value, VideoBillingState.voided.value):
            return False
        if job.status == VideoJobStatus.completed.value:
            await _settle_hold(db, job)
        else:
            await _void_hold(db, job, "stranded_hold", "Hold of a failed video released by the sweeper")
        job.updated_at = _utcnow()
        await db.commit()
    return job.billing_state in (VideoBillingState.charged.value, VideoBillingState.voided.value)


async def _sweep_close(row: Any) -> bool:
    """One on-chain close retry of a sweeper pass, bounded by
    SWEEP_CLOSE_TIMEOUT_SECONDS. A close cut by the timeout goes back to OPEN
    and its row behind the other retries."""
    try:
        return await asyncio.wait_for(
            _close_job_session(row.id, row.session_id, f"video_{row.status}"),
            timeout=SWEEP_CLOSE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("Video session close timed out; the sweeper retries it",
                       video_id=row.id,
                       session_id=row.session_id,
                       timeout_seconds=SWEEP_CLOSE_TIMEOUT_SECONDS,
                       event_type="video_session_close_timeout")
        await _requeue_close(row.id)
        raise


async def _sweep_reclaim(session_id: str) -> bool:
    """Close again a video session left CLOSING by a closer that died,
    bounded as _sweep_close. Its closer had decided the close, so this only
    finishes it."""
    return await asyncio.wait_for(
        session_routing_service.close_session_now(session_id, reason="video_close_abandoned"),
        timeout=SWEEP_CLOSE_TIMEOUT_SECONDS,
    )


async def sweep() -> Dict[str, int]:
    """
    One sweeper pass (the caller holds the advisory lock), in this order:
    1. refresh queued / in_progress rows (settle or void their holds);
    2. fail rows pending without progress for pending_timeout_seconds() as
       submit_interrupted (their sessions are left to the expiry close);
    3. finish the billing of terminal rows whose hold is still pending;
    4. stamp the sessions of unconfirmed submits that the expiry close ended;
    5. last, retry the on-chain closes of terminal rows, and claim again the
       video sessions left CLOSING by a closer that died (those of a submit
       that lost its row have no video_jobs row to retry them): at most
       SWEEP_CLOSE_BATCH_SIZE of each, together, each bounded by
       SWEEP_CLOSE_TIMEOUT_SECONDS, so a slow chain delays none of the above.
    """
    summary = {"refreshed": 0, "interrupted": 0, "sessions_closed": 0, "holds_released": 0, "errors": 0}
    terminal = list(VIDEO_JOB_TERMINAL_STATUSES)

    async with get_db() as db:
        # Every active row each pass: a no-op refresh leaves updated_at as it
        # is, so a batch limit would keep polling the same rows
        active = (await db.execute(
            select(VideoJob.id)
            .where(VideoJob.status.in_([VideoJobStatus.queued.value, VideoJobStatus.in_progress.value]))
            .order_by(VideoJob.created_at.asc())
        )).scalars().all()
    for video_id in active:
        try:
            # The pass closes the sessions of rows this ends in step 5
            await refresh_job(video_id, close_session=False)
            summary["refreshed"] += 1
        except Exception as e:
            summary["errors"] += 1
            # The class only: a failed UPDATE's text can carry video_url
            logger.error("Error refreshing video job",
                         video_id=video_id,
                         error_class=type(e).__name__,
                         event_type="video_sweep_refresh_error")

    async with get_db() as db:
        stuck = (await db.execute(
            select(VideoJob.id)
            .where(
                VideoJob.status == VideoJobStatus.pending.value,
                VideoJob.updated_at < _utcnow() - timedelta(seconds=pending_timeout_seconds()),
            )
            .order_by(VideoJob.updated_at.asc())
            .limit(SWEEP_BATCH_SIZE)
        )).scalars().all()
    for video_id in stuck:
        try:
            job = await _interrupt_pending(video_id)
            if job is not None and job.error_code == SUBMIT_INTERRUPTED:
                summary["interrupted"] += 1
                logger.warning("Video submission interrupted",
                               video_id=video_id,
                               user_id=job.user_id,
                               billing_state=job.billing_state,
                               session_id=job.session_id,
                               event_type="video_submit_interrupted")
        except Exception as e:
            summary["errors"] += 1
            logger.error("Error failing interrupted video job",
                         video_id=video_id,
                         error=str(e),
                         event_type="video_sweep_interrupt_error")

    async with get_db() as db:
        stranded = (await db.execute(
            select(VideoJob.id)
            .join(CreditLedger, CreditLedger.id == VideoJob.ledger_entry_id)
            .where(
                VideoJob.status.in_(terminal),
                VideoJob.billing_state.notin_(
                    [VideoBillingState.charged.value, VideoBillingState.voided.value]
                ),
                CreditLedger.endpoint == VIDEO_LEDGER_ENDPOINT,
                CreditLedger.entry_type == LedgerEntryType.usage_hold,
                CreditLedger.status == LedgerStatus.pending,
            )
            .limit(SWEEP_BATCH_SIZE)
        )).scalars().all()
    for video_id in stranded:
        try:
            if await _release_stranded_hold(video_id):
                summary["holds_released"] += 1
                logger.warning("Stranded video hold released",
                               video_id=video_id,
                               event_type="video_stranded_hold_released")
        except Exception as e:
            summary["errors"] += 1
            logger.error("Error releasing stranded video hold",
                         video_id=video_id,
                         error_class=type(e).__name__,
                         event_type="video_sweep_hold_error")

    unclosed = (
        VideoJob.session_id.isnot(None),
        VideoJob.session_closed_at.is_(None),
        VideoJob.status.in_(terminal),
    )
    columns = (VideoJob.id, VideoJob.session_id, VideoJob.status)
    async with get_db() as db:
        # Unconfirmed submits: only the expiry close at EndsAt ends their
        # sessions, so nothing is sent on chain for them
        waiting = (await db.execute(
            select(*columns)
            .where(*unclosed, VideoJob.node_job_id.is_(None), VideoJob.error_code == SUBMIT_INTERRUPTED)
            .order_by(VideoJob.updated_at.asc())
            .limit(SWEEP_BATCH_SIZE)
        )).all()
        closable = (await db.execute(
            select(*columns)
            .where(*unclosed, _session_closable_clause())
            .order_by(VideoJob.updated_at.asc())
            .limit(SWEEP_CLOSE_BATCH_SIZE)
        )).all()

    for row in waiting:
        try:
            if await _close_job_session(row.id, row.session_id, f"video_{row.status}", close_now=False):
                summary["sessions_closed"] += 1
        except Exception as e:
            summary["errors"] += 1
            logger.error("Error reading a video session state",
                         video_id=row.id,
                         session_id=row.session_id,
                         error=str(e),
                         event_type="video_sweep_close_error")

    retried = {row.session_id for row in closable}
    try:
        abandoned_ids = await session_routing_service.list_abandoned_video_closes(SWEEP_CLOSE_BATCH_SIZE)
    except Exception as e:
        # A failed read must not cost this pass its close retries.
        logger.error("Error listing abandoned video session closes",
                     error_class=type(e).__name__,
                     event_type="video_sweep_close_error")
        abandoned_ids = []
    abandoned = [session_id for session_id in abandoned_ids if session_id not in retried]

    results = await asyncio.gather(
        *(_sweep_close(row) for row in closable),
        *(_sweep_reclaim(session_id) for session_id in abandoned),
        return_exceptions=True,
    )
    for row, result in zip(closable, results):
        if result is True:
            summary["sessions_closed"] += 1
        elif isinstance(result, BaseException):
            summary["errors"] += 1
            if not isinstance(result, asyncio.TimeoutError):
                logger.error("Error closing video session",
                             video_id=row.id,
                             session_id=row.session_id,
                             error_class=type(result).__name__,
                             error=str(result),
                             event_type="video_sweep_close_error")
    for session_id, result in zip(abandoned, results[len(closable):]):
        if result is True:
            summary["sessions_closed"] += 1
            logger.warning("Abandoned video session close finished",
                           session_id=session_id,
                           event_type="video_abandoned_close_finished")
        elif isinstance(result, BaseException):
            summary["errors"] += 1
            logger.error("Error closing an abandoned video session",
                         session_id=session_id,
                         error_class=type(result).__name__,
                         error=str(result),
                         event_type="video_sweep_close_error")

    return summary
