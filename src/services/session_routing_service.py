"""
Session Routing Service

This service implements intelligent session routing with automatic scaling:
1. Routes authorized inference requests to open sessions
2. Opens new sessions when no sessions exist or all are utilized
3. Runs an automated activity loop for preferred model management

Key concepts:
- Model: identifier (e.g., "llama-3.3-70b" or modelID hex)
- Session: connection slot to a provider for a model
- Open session: session in state OPEN
- Utilized session: session with active_requests > 0
- Preferred model: configured list for automatic session pre-warming
"""

import asyncio
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional, Dict, Any, List

from sqlalchemy import select, update, func, and_, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import RoutedSession, SessionState
from ..db.database import get_db, advisory_xact_lock
from ..core.config import settings
from ..core.model_routing import model_router
from ..core.logging_config import get_api_logger
from ..services import proxy_router_service

logger = get_api_logger()


class SessionRoutingError(Exception):
    """Base exception for session routing errors."""
    
    def __init__(self, message: str, model_id: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.model_id = model_id


class NoSessionAvailableError(SessionRoutingError):
    """Raised when no session can be acquired for a request."""
    pass


class SessionOpenError(SessionRoutingError):
    """Raised when session opening fails."""
    pass


class SessionRoutingService:
    """
    Service for routing requests to sessions and managing session lifecycle.
    
    Thread-safety:
    - On-chain session ops use an ADAPTIVE wallet throttle per replica: they run
      concurrently on the happy path, and serialize on a single wallet lock only
      after a nonce conflict is observed (see _run_onchain)
    - Database operations use row-level locking where needed
    """

    def __init__(self):
        # Adaptive on-chain wallet throttle (per replica). The single consumer
        # wallet has NO nonce lock in the C-Node (it fetches PendingNonceAt per
        # tx), so a simultaneous BURST of txs (a failover storm: many open+close
        # at once across models) collides on nonce ("replacement transaction
        # underpriced") — while normal, time-staggered load sequences fine.
        # So we do NOT serialize on the happy path; instead, when a nonce
        # conflict is observed we open a short THROTTLED window during which all
        # on-chain ops serialize on this lock (draining the burst in nonce
        # order). See _run_onchain / _is_throttled.
        self._wallet_lock = asyncio.Lock()
        # Monotonic deadline; while time.monotonic() < this, the wallet is
        # throttled (serialized). Each new nonce conflict slides it forward.
        self._throttle_until = 0.0
        self._throttle_cooldown = settings.SESSION_ONCHAIN_THROTTLE_COOLDOWN_SECONDS

        # Automation loop control
        self._automation_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

        # Strong refs to fire-and-forget close tasks (event loop keeps only
        # weak refs; an unreferenced task can be GC'd before it runs).
        self._background_close_tasks: set = set()

        # Short-lived per-model "is this an expensive model?" cache. The tier
        # decision needs the model's lowest rated-bid price (an on-chain read
        # via the proxy-router); cache it so neither the open path nor the
        # per-tick automation loop hammers that lookup. Keyed by model_id ->
        # (monotonic_expiry, is_expensive). Per-replica and deterministic from
        # the cutoff + price, so no cross-replica coordination is needed.
        self._expensive_tier_cache: Dict[str, tuple] = {}
        self._expensive_tier_cache_ttl = 300.0

        logger.info("SessionRoutingService initialized",
                   event_type="session_routing_service_init")
    
    def _get_preferred_models(self) -> set:
        """Get the set of preferred model IDs from configuration."""
        if not settings.SESSION_PREFERRED_MODELS:
            return set()
        return set(m.strip() for m in settings.SESSION_PREFERRED_MODELS.split(",") if m.strip())

    # =========================================================================
    # EXPENSIVE-MODEL TIER
    # =========================================================================

    async def _get_model_min_price_per_second(self, model_id: str) -> Optional[float]:
        """Lowest rated-bid price for a model in MOR/sec, or None on failure.

        Best-effort: never raises. A price lookup must never block (or fail) a
        session open — callers treat None as "not expensive" and fall back to
        the global session settings. Bid PricePerSecond is in wei (1e18 = 1 MOR).
        """
        try:
            response = await proxy_router_service.getRatedBids(model_id)
            result = response.json()
            if isinstance(result, dict):
                bids = result.get("bids", []) or []
            elif isinstance(result, list):
                bids = result
            else:
                bids = []

            prices: List[float] = []
            for bid in bids:
                if not isinstance(bid, dict):
                    continue
                # /bids/rated wraps each entry as {"ID":..., "Bid": {...,
                # "PricePerSecond": ...}, "Score": ...}. Read the nested Bid,
                # falling back to the flat shape for robustness.
                inner = bid.get("Bid") if isinstance(bid.get("Bid"), dict) else bid
                pps = inner.get("PricePerSecond")
                if pps is None:
                    continue
                try:
                    prices.append(int(str(pps)) / 1e18)
                except (ValueError, TypeError):
                    continue

            return min(prices) if prices else None
        except Exception as e:
            logger.warning("Rated-bid price lookup failed; treating model as non-expensive",
                           model_id=model_id,
                           error=str(e),
                           event_type="expensive_tier_price_lookup_error")
            return None

    async def _is_expensive_model(self, model_id: str) -> bool:
        """True if the model's lowest rated bid is >= the expensive cutoff.

        Returns False when the tier is disabled (cutoff <= 0) or the price
        cannot be determined. Result is cached per model for a short TTL so the
        on-chain bid read is not repeated on every open / automation tick.
        """
        cutoff = settings.SESSION_EXPENSIVE_CUTOFF_MOR_PER_SECOND
        if cutoff <= 0:
            return False

        cached = self._expensive_tier_cache.get(model_id)
        if cached is not None and time.monotonic() < cached[0]:
            return cached[1]

        price = await self._get_model_min_price_per_second(model_id)
        is_expensive = price is not None and price >= cutoff
        self._expensive_tier_cache[model_id] = (
            time.monotonic() + self._expensive_tier_cache_ttl,
            is_expensive,
        )
        return is_expensive

    # =========================================================================
    # ADAPTIVE ON-CHAIN WALLET THROTTLE
    # =========================================================================

    def _is_throttled(self) -> bool:
        """True while the wallet is in its post-nonce-conflict cooldown window."""
        return time.monotonic() < self._throttle_until

    def _engage_throttle(self) -> None:
        """(Re)open the THROTTLED window; sustained conflicts keep sliding it."""
        self._throttle_until = time.monotonic() + self._throttle_cooldown

    async def _run_onchain(
        self,
        op_factory: Callable[[], Awaitable[Any]],
        *,
        op_name: str,
        op_logger,
    ) -> Any:
        """
        Run a single on-chain wallet op (open/close) with adaptive throttling.

        The consumer wallet has no nonce lock in the C-Node, so a simultaneous
        burst collides on nonce while normal/staggered load is fine. Therefore:

        - Happy path (not throttled): run the op directly, fully concurrent.
        - While throttled: acquire the wallet lock so the burst drains in nonce
          order.
        - On a nonce conflict (not yet throttled): open the THROTTLED window so
          subsequent ops serialize, then retry THIS op once under the lock (a
          serialized retry lands on a fresh, uncontended nonce).

        op_factory must build a fresh awaitable on each call (it may be retried).
        Only the on-chain call belongs inside op_factory — never a DB connection,
        so a throttled queue can grow without pinning the DB pool.
        """
        if self._is_throttled():
            async with self._wallet_lock:
                return await op_factory()

        try:
            return await op_factory()
        except proxy_router_service.ProxyRouterServiceError as e:
            if not proxy_router_service.is_nonce_error(str(e)):
                raise
            # Burst detected: throttle subsequent ops and retry this one once,
            # serialized, after a brief jittered settle so the prior tx registers.
            self._engage_throttle()
            op_logger.warning(
                "On-chain nonce conflict; throttling wallet and retrying serialized",
                op=op_name,
                cooldown_s=self._throttle_cooldown,
                event_type="onchain_nonce_throttle",
            )
            await asyncio.sleep(0.2 + random.random() * 0.3)
            async with self._wallet_lock:
                return await op_factory()

    # =========================================================================
    # REQUEST PATH: Route requests to sessions
    # =========================================================================
    
    async def route_request(
        self,
        user_id: int,
        requested_model: Optional[str] = None,
        model_type: str = "LLM",
        omit_provider: Optional[str] = None,
    ) -> str:
        """
        Route an authorized request to an appropriate session.

        Main entry point for the request path. Sessions are shared across
        users - user_id is only used for private key lookup when opening a new
        session.

        Manages its own short-lived DB connections and holds NO DB connection
        while waiting on the wallet lock / the slow on-chain openSession call.
        This means any number of concurrent requests can queue for an open
        without pinning the DB pool (the wallet lock itself is the queue).

        Args:
            omit_provider: Provider address (0x hex) to avoid — set by
                failover after a prompt failure. Idle sessions on that
                provider are skipped and a newly opened session excludes it
                from the proxy-router's bid selection.

        Returns:
            str: Session ID to use for the request

        Raises:
            NoSessionAvailableError: If no session could be acquired
            SessionOpenError: If session opening failed
        """
        route_logger = logger.bind(
            user_id=user_id,
            requested_model=requested_model,
            model_type=model_type,
            omit_provider=omit_provider,
        )

        # Resolve model to blockchain ID
        model_id = await model_router.get_target_model(requested_model, type=model_type)
        route_logger = route_logger.bind(model_id=model_id)

        route_logger.info("Routing request to session",
                         event_type="route_request_start")

        # FAST PATH (lock-free): atomically claim an idle OPEN session for this
        # model with a single UPDATE ... FOR UPDATE SKIP LOCKED on its own
        # short-lived connection. The DB row lock (not an in-process lock) hands
        # a session to exactly one request, correct across replicas, and the
        # connection is released immediately (never held across the open below).
        async with get_db() as db:
            claimed_id = await self._claim_idle_session(db, model_id, omit_provider=omit_provider)
        if claimed_id is not None:
            route_logger.info("Routed to idle session (atomic claim)",
                             session_id=claimed_id,
                             event_type="route_to_unutilized")
            return claimed_id

        # OPEN PATH: no idle session -> open a new paid on-chain session. The tx
        # is serialized on the wallet lock inside _open_session_for_model (one
        # consumer wallet -> one nonce sequence). NO DB connection is held while
        # waiting on that lock, so any number of concurrent openers may queue
        # without exhausting the DB pool. The session is created already assigned
        # to this request (active_requests=1), so it is never momentarily visible
        # as idle - and thus claimable by another request - between insert and
        # use.
        route_logger.info("No idle session for model, opening a new one",
                         event_type="no_idle_session")
        return await self._open_session_for_model(
            model_id=model_id,
            model_name=requested_model,
            model_type=model_type,
            user_id=user_id,
            initial_active_requests=1,
            omit_provider=omit_provider,
        )

    async def release_session(self, db: AsyncSession, session_id: str) -> None:
        """
        Release a session after request completion.
        
        Decrements active_requests counter atomically.
        
        Args:
            db: Database session
            session_id: Session ID to release
        """
        release_logger = logger.bind(session_id=session_id)
        
        try:
            # Atomic decrement of active_requests (never go below 0)
            await db.execute(
                update(RoutedSession)
                .where(
                    RoutedSession.id == session_id,
                    RoutedSession.active_requests > 0
                )
                .values(
                    active_requests=RoutedSession.active_requests - 1,
                    updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
                )
            )
            await db.commit()
            
            release_logger.debug("Session released",
                               event_type="session_released")
            
        except Exception as e:
            release_logger.error("Error releasing session",
                               error=str(e),
                               event_type="session_release_error",
                               exc_info=True)
            await db.rollback()
    
    async def invalidate_session(
        self,
        db: AsyncSession,
        session_id: str,
        reason: str,
        state: SessionState = SessionState.FAILED,
    ) -> bool:
        """
        Mark a session FAILED (provider failover) or EXPIRED (session
        renewal) so it is never routed to again, and close it on the proxy
        router in the background (best-effort).

        Marking matters in both cases: the row's DB expires_at can lie in
        the future (on-chain endsAt may pass earlier), so a released-only
        row would keep being re-picked by route_request.

        Unlike _close_session this works while the session is still
        utilized (the failing request itself holds an assignment) and never
        blocks the caller on the on-chain close transaction.

        Returns True if this call transitioned the session out of OPEN.
        """
        invalidate_logger = logger.bind(session_id=session_id, target_state=state.value)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            result = await db.execute(
                update(RoutedSession)
                .where(
                    RoutedSession.id == session_id,
                    RoutedSession.state == SessionState.OPEN.value,
                )
                .values(
                    state=state.value,
                    error_reason=f"recovery: {reason}"[:500],
                    updated_at=now,
                )
            )
            await db.commit()
        except Exception as e:
            invalidate_logger.error("Error invalidating session",
                                    error=str(e),
                                    event_type="session_invalidate_error",
                                    exc_info=True)
            await db.rollback()
            return False

        transitioned = (getattr(result, "rowcount", 0) or 0) > 0
        if transitioned:
            invalidate_logger.warning("Session invalidated after prompt failure",
                                      reason=reason,
                                      event_type="session_invalidated")
            # Close on-chain in the background: close needs a (possibly
            # failing) provider-report RPC plus a blockchain tx — too slow
            # to block the user's retry on. closeSession tolerates
            # already-closed sessions.
            task = asyncio.create_task(self._close_invalidated_session(session_id))
            self._background_close_tasks.add(task)
            task.add_done_callback(self._background_close_tasks.discard)
        return transitioned

    async def _close_invalidated_session(self, session_id: str) -> None:
        """Best-effort proxy-router close for an invalidated session."""
        try:
            # Same wallet/nonce sequence as opens -> route through the adaptive
            # throttle so a storm's closes don't collide with its opens.
            await self._run_onchain(
                lambda: proxy_router_service.closeSession(session_id),
                op_name="closeSession",
                op_logger=logger,
            )
            logger.info("Invalidated session closed on proxy router",
                        session_id=session_id,
                        event_type="invalidated_session_closed")
        except Exception as e:
            # The proxy-router's SessionExpiryHandler will close it after
            # EndsAt anyway; losing this close only delays stake recovery.
            logger.warning("Best-effort close of invalidated session failed",
                           session_id=session_id,
                           error=str(e),
                           event_type="invalidated_session_close_error")

    @asynccontextmanager
    async def session_context(
        self,
        user_id: int,
        requested_model: Optional[str] = None,
        model_type: str = "LLM"
    ):
        """
        Context manager for session routing with automatic release.
        
        Usage:
            async with session_routing_service.session_context(user_id, model) as session_id:
                # Use session_id for request
                ...
            # Session automatically released on exit
        """
        session_id = await self.route_request(
            user_id, requested_model, model_type
        )

        try:
            yield session_id
        finally:
            async with get_db() as db:
                await self.release_session(db, session_id)
    
    # =========================================================================
    # SESSION LIFECYCLE: Open and close sessions
    # =========================================================================
    
    @staticmethod
    def _parse_onchain_ends_at(status: Any) -> Optional[int]:
        """Extract the on-chain endsAt (unix seconds) from a getSessionStatus body.

        The C-Node serialises its Session struct with Go field names (no json
        tags), so the field is ``EndsAt`` nested under ``session``. Accept a few
        shapes/casings defensively and return None if absent/invalid so callers
        can fall back rather than raise.
        """
        if not isinstance(status, dict):
            return None
        session = status.get("session") or status.get("Session") or status
        if not isinstance(session, dict):
            return None
        raw = session.get("EndsAt", session.get("endsAt"))
        if raw is None:
            return None
        try:
            ends_at = int(raw)
        except (TypeError, ValueError):
            return None
        return ends_at if ends_at > 0 else None

    @staticmethod
    def _parse_provider_address(status: Any) -> Optional[str]:
        """Extract the provider address (0x hex) from a getSessionStatus body.

        Same defensive shape handling as _parse_onchain_ends_at. Returns None
        when absent/invalid — provider tracking is best-effort and must never
        block a session open.
        """
        if not isinstance(status, dict):
            return None
        session = status.get("session") or status.get("Session") or status
        if not isinstance(session, dict):
            return None
        raw = session.get("Provider", session.get("provider"))
        if not isinstance(raw, str):
            return None
        raw = raw.strip()
        if len(raw) == 42 and raw.startswith("0x"):
            return raw
        return None

    async def _fetch_session_status(self, session_id: str, open_logger) -> Optional[Any]:
        """Best-effort read of the freshly-opened session's on-chain status."""
        try:
            return await proxy_router_service.getSessionStatus(session_id)
        except Exception as e:
            open_logger.warning(
                "Could not read session status from proxy router",
                error=str(e),
                event_type="expires_at_read_error",
            )
            return None

    def _resolve_expires_at(
        self, status: Any, fallback: datetime, open_logger
    ) -> datetime:
        """Return expires_at anchored to the on-chain endsAt (+ configured buffer).

        Parses the session's on-chain endsAt from ``status`` and adds
        SESSION_EXPIRY_BUFFER_SECONDS so the cleanup sweep closes the
        session at/after its true end (a "late" close → full stake back to the
        wallet). Best-effort: any failure returns ``fallback`` (the call-start
        estimate) so a session open is never blocked on this read.
        """
        ends_at = self._parse_onchain_ends_at(status)

        if ends_at is None:
            open_logger.warning(
                "on-chain endsAt missing/invalid; using estimated expiry",
                event_type="expires_at_missing",
            )
            return fallback

        expires_at = datetime.fromtimestamp(ends_at, tz=timezone.utc).replace(
            tzinfo=None
        ) + timedelta(seconds=settings.SESSION_EXPIRY_BUFFER_SECONDS)
        open_logger.info(
            "Anchored expires_at to on-chain endsAt",
            onchain_ends_at=ends_at,
            buffer_seconds=settings.SESSION_EXPIRY_BUFFER_SECONDS,
            event_type="expires_at_onchain",
        )
        return expires_at

    async def _open_session_for_model(
        self,
        model_id: str,
        model_name: Optional[str] = None,
        model_type: str = "LLM",
        user_id: Optional[int] = None,
        initial_active_requests: int = 0,
        omit_provider: Optional[str] = None,
    ) -> str:
        """
        Open a new on-chain session for a model and persist its row.

        Sessions are shared across users. The user_id is only used for private
        key lookup when calling the proxy router.

        Holds NO DB connection while it waits on the wallet lock / the slow
        on-chain openSession call - the row insert uses its own short-lived
        connection afterwards. This lets any number of concurrent openers queue
        on the wallet lock without pinning the DB pool.

        DB row is only created after a successful open (no OPENING state).

        Args:
            model_id: Blockchain model ID (hex string)
            model_name: Human-readable model name
            model_type: Type of model
            user_id: Optional user ID for private key lookup (uses fallback if not provided)
            initial_active_requests: active_requests to create the row with. The
                request path passes 1 so the session is created already assigned
                to its caller (never momentarily idle / claimable by another
                request between insert and use); the automation path passes 0 to
                create an idle, pre-warmed session.
            omit_provider: Provider address to exclude from the proxy-router's
                bid selection (failover away from an impaired provider).

        Returns:
            str: The blockchain session id of the newly opened session.

        Raises:
            SessionOpenError: If session opening fails
        """
        open_logger = logger.bind(
            model_id=model_id,
            model_name=model_name
        )

        open_logger.info("Opening new session for model",
                        event_type="session_open_start")

        # Expensive models open with a shorter duration so the amplified
        # on-chain stake pulled per session stays small (more concurrent
        # premium sessions before the shared wallet is exhausted). Best-effort:
        # if the tier is disabled or the price can't be read, use the global
        # default duration.
        is_expensive = await self._is_expensive_model(model_id)
        session_duration = (
            settings.SESSION_EXPENSIVE_DEFAULT_DURATION_SECONDS
            if is_expensive
            else settings.SESSION_DEFAULT_DURATION_SECONDS
        )
        open_logger = open_logger.bind(
            expensive_tier=is_expensive,
            session_duration=session_duration,
        )
        # Fallback ONLY. The authoritative expiry is the actual on-chain endsAt,
        # read back after the open (see below). This call-start estimate is used
        # solely if that read fails, because it is systematically too short: it
        # ignores how long the open tx takes to mine, so the on-chain
        # openedAt (== mine time) is later and the true endsAt is later still.
        # Closing at this estimate lands a few seconds BEFORE endsAt under load,
        # turning an intended "late" close into an early one — which locks the
        # elapsed-time stipend in userStakesOnHold and forces housekeeping to
        # recover it via withdrawUserStakes.
        estimated_expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=session_duration)
        )

        try:
            # Call proxy router to open session
            # Uses user's private key if provided, otherwise uses fallback key
            open_logger.info("Calling proxy router to open session",
                           user_id=user_id,
                           event_type="proxy_open_session_start")

            # Adaptive throttle: open concurrently on the happy path; serialize
            # on the wallet lock only while throttled (after a nonce conflict).
            # No DB connection is held across this call (the row insert below
            # uses its own short-lived session), so a throttled queue can grow
            # without exhausting the DB pool.
            response = await self._run_onchain(
                lambda: proxy_router_service.openSession(
                    target_model=model_id,
                    session_duration=session_duration,
                    failover=False,
                    direct_payment=False,
                    omit_provider=omit_provider,
                ),
                op_name="openSession",
                op_logger=open_logger,
            )

            blockchain_session_id = response.get("sessionID")

            if not blockchain_session_id:
                raise SessionOpenError(
                    "No session ID returned from proxy router",
                    model_id=model_id
                )

            # Read the session's on-chain status once and use it for both the
            # authoritative expiry and the provider address. Best-effort: any
            # failure falls back to the call-start expiry estimate and a NULL
            # provider, so an open is never blocked on this read.
            status = await self._fetch_session_status(blockchain_session_id, open_logger)

            # Anchor expires_at to the REAL on-chain endsAt (+ small buffer) so
            # the cleanup sweep closes the session AT/AFTER endsAt (a "late"
            # close returns the full stake straight to the wallet).
            expires_at = self._resolve_expires_at(
                status, estimated_expires_at, open_logger
            )

            # Provider serving this session — lets failover exclude it later
            # (omitProvider) instead of possibly reopening on the same
            # impaired provider.
            provider_address = self._parse_provider_address(status)

            # Create session record only after successful open
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            endpoint = "/v1/chat/completions"
            if model_type == "EMBEDDINGS":
                endpoint = "/v1/embeddings"
            elif model_type == "AUTOMATION":
                endpoint = ""

            # Persist on a fresh short-lived connection. Create the row already
            # assigned (active_requests=initial_active_requests) so a request-path
            # session (1) is never visible as idle - and thus claimable by a
            # concurrent request via _claim_idle_session - between insert and use.
            async with get_db() as db:
                session = RoutedSession(
                    id=blockchain_session_id,
                    model_id=model_id,
                    model_name=model_name,
                    state=SessionState.OPEN,
                    expires_at=expires_at,
                    active_requests=initial_active_requests,
                    created_at=now,
                    updated_at=now,
                    endpoint=endpoint,
                    provider_address=provider_address
                )
                db.add(session)
                await db.commit()

            open_logger.info("Session opened successfully",
                           session_id=blockchain_session_id,
                           event_type="session_opened")

            return blockchain_session_id

        except SessionOpenError:
            # Already a clean domain error (e.g. no session id returned).
            raise
        except proxy_router_service.ProxyRouterServiceError as e:
            open_logger.error("Proxy router error opening session",
                            error=str(e),
                            event_type="session_open_proxy_error")

            raise SessionOpenError(
                f"Failed to open session: {e.message}",
                model_id=model_id
            ) from e

        except Exception as e:
            open_logger.error("Error opening session",
                            error=str(e),
                            event_type="session_open_error",
                            exc_info=True)

            raise SessionOpenError(
                f"Failed to open session: {str(e)}",
                model_id=model_id
            ) from e
    
    async def _close_session(
        self,
        db: AsyncSession,
        session: RoutedSession
    ) -> bool:
        """
        Close a session safely.
        
        Args:
            db: Database session
            session: Session to close
            
        Returns:
            bool: True if closed successfully
        """
        close_logger = logger.bind(session_id=session.id, model_id=session.model_id)
        
        # Safety check: don't close utilized sessions
        if session.is_utilized:
            close_logger.warning("Cannot close utilized session",
                               active_requests=session.active_requests,
                               event_type="close_utilized_session_rejected")
            return False
        
        # Mark as CLOSING
        session.state = SessionState.CLOSING
        session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        
        close_logger.info("Closing session",
                         event_type="session_close_start")
        
        try:
            # Close on-chain via the adaptive throttle (shared wallet/nonce
            # with every other on-chain session op on this replica).
            await self._run_onchain(
                lambda: proxy_router_service.closeSession(session.id),
                op_name="closeSession",
                op_logger=close_logger,
            )

            # Mark as CLOSED
            session.state = SessionState.CLOSED
            session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()
            
            close_logger.info("Session closed successfully",
                            event_type="session_closed")
            return True
            
        except proxy_router_service.ProxyRouterServiceError as e:
            # Mark as FAILED but leave the error reason
            session.state = SessionState.FAILED
            session.error_reason = f"Close failed: {str(e)}"
            session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()
            
            close_logger.error("Error closing session",
                             error=str(e),
                             event_type="session_close_error")
            return False
        
        except Exception as e:
            # Mark as FAILED
            session.state = SessionState.FAILED
            session.error_reason = f"Close failed: {str(e)}"
            session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()
            
            close_logger.error("Unexpected error closing session",
                             error=str(e),
                             event_type="session_close_unexpected_error",
                             exc_info=True)
            return False
    
    async def _claim_idle_session(
        self,
        db: AsyncSession,
        model_id: str,
        omit_provider: Optional[str] = None,
    ) -> Optional[str]:
        """
        Atomically claim one idle (active_requests == 0), OPEN, non-expired
        session for a model and increment its active_requests in a single round
        trip.

        Implemented as
            UPDATE routed_sessions SET active_requests = active_requests + 1
            WHERE id = (SELECT id ... FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id
        so that:
        - a given idle session is claimed by exactly one request, even across
          processes/replicas (the row lock, not an in-process asyncio.Lock,
          provides the mutual exclusion);
        - concurrent claimers SKIP each other's locked rows instead of blocking
          or double-assigning the same "unutilized" session (the old read-all +
          Python-pick + separate UPDATE could double-assign across replicas);
        - routing the common case costs one statement served by
          idx_routed_sessions_model_state, with no lock held across I/O.

        When ``omit_provider`` is set (failover), idle sessions known to belong
        to that provider are skipped so the retry doesn't land on a sibling
        session of the same impaired provider. Rows with an unknown (NULL)
        provider stay claimable — optimistic, matches pre-tracking behavior.

        Returns the claimed session id, or None when no idle session exists.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stmt = text(
            """
            UPDATE routed_sessions
            SET active_requests = active_requests + 1,
                last_used_at = :now,
                updated_at = :now
            WHERE id = (
                SELECT id FROM routed_sessions
                WHERE model_id = :model_id
                  AND state = :open_state
                  AND active_requests = 0
                  AND expires_at > :now
                  AND (
                        CAST(:omit_provider AS VARCHAR) IS NULL
                        OR provider_address IS NULL
                        OR lower(provider_address) != lower(:omit_provider)
                      )
                ORDER BY last_used_at ASC NULLS FIRST
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id
            """
        )
        try:
            result = await db.execute(
                stmt,
                {
                    "now": now,
                    "model_id": model_id,
                    "open_state": SessionState.OPEN.value,
                    "omit_provider": omit_provider,
                },
            )
            claimed_id = result.scalar_one_or_none()
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return claimed_id

    # =========================================================================
    # QUERY HELPERS
    # =========================================================================
    
    async def _get_open_sessions_for_model(
        self,
        db: AsyncSession,
        model_id: str
    ) -> List[RoutedSession]:
        """Get all OPEN and non-expired sessions for a model."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        result = await db.execute(
            select(RoutedSession)
            .where(
                RoutedSession.model_id == model_id,
                RoutedSession.state == SessionState.OPEN,
                RoutedSession.expires_at > now  # Filter out expired sessions
            )
            .order_by(RoutedSession.last_used_at.asc().nullsfirst())
        )
        return list(result.scalars().all())
    
    async def _get_all_open_sessions(
        self,
        db: AsyncSession
    ) -> List[RoutedSession]:
        """Get all OPEN and non-expired sessions across all models."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        result = await db.execute(
            select(RoutedSession)
            .where(
                RoutedSession.state == SessionState.OPEN
            )
        )
        return list(result.scalars().all())
    
    async def _get_sessions_by_model(
        self,
        db: AsyncSession
    ) -> Dict[str, List[RoutedSession]]:
        """Get all OPEN sessions grouped by model_id."""
        sessions = await self._get_all_open_sessions(db)
        
        by_model: Dict[str, List[RoutedSession]] = {}
        for session in sessions:
            if session.model_id not in by_model:
                by_model[session.model_id] = []
            by_model[session.model_id].append(session)
        
        return by_model
    
    # =========================================================================
    # AUTOMATED ACTIVITY LOOP
    # =========================================================================
    
    async def start_automation_loop(self) -> None:
        """Start the automated activity loop."""
        if self._automation_task is not None:
            logger.warning("Automation loop already running",
                          event_type="automation_loop_already_running")
            return
        
        self._shutdown_event.clear()
        self._automation_task = asyncio.create_task(self._automation_loop())
        
        logger.info("Automation loop started",
                   interval_seconds=settings.SESSION_AUTOMATION_INTERVAL_SECONDS,
                   event_type="automation_loop_started")
    
    async def stop_automation_loop(self) -> None:
        """Stop the automated activity loop."""
        if self._automation_task is None:
            return
        
        self._shutdown_event.set()
        self._automation_task.cancel()
        
        try:
            await self._automation_task
        except asyncio.CancelledError:
            pass
        
        self._automation_task = None
        logger.info("Automation loop stopped",
                   event_type="automation_loop_stopped")
    
    async def _automation_loop(self) -> None:
        """
        Automated activity loop for session management.
        
        Runs periodically and applies scaling logic based on:
        - Preferred models: Keep at least one idle session, scale up if all utilized
        - Non-preferred models: Close idle sessions to free resources
        """
        auto_logger = logger.bind(component="automation_loop")
        
        auto_logger.info("Automation loop starting",
                        event_type="automation_loop_start")
        
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(settings.SESSION_AUTOMATION_INTERVAL_SECONDS)
                
                if self._shutdown_event.is_set():
                    break
                
                auto_logger.debug("Running automation cycle",
                                event_type="automation_cycle_start")
                
                # Leader election: only one replica runs the scaling cycle per
                # tick. Without this, every replica's loop wakes on the same
                # shared state and can open/close duplicate paid blockchain
                # sessions (see efficiency audit H15/H16).
                async with advisory_xact_lock("session_automation") as is_leader:
                    if not is_leader:
                        auto_logger.debug(
                            "Skipping automation cycle - another replica holds the leader lock",
                            event_type="automation_cycle_skipped_not_leader")
                        continue
                    
                    async with get_db() as db:
                        await self._run_automation_cycle(db)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                auto_logger.error("Error in automation cycle",
                                error=str(e),
                                event_type="automation_cycle_error",
                                exc_info=True)
    
    async def _run_automation_cycle(self, db: AsyncSession) -> None:
        """
        Run one cycle of the automation loop.
        
        For each model with open sessions:
        - If preferred: ensure at least one idle, scale up if all utilized
        - If not preferred: close idle sessions
        """
        auto_logger = logger.bind(component="automation_cycle")
        
        preferred_models = self._get_preferred_models()
        sessions_by_model = await self._get_sessions_by_model(db)
        
        # Also check for expired sessions
        await self._cleanup_expired_sessions(db)
        
        # Process each model
        for model_id, sessions in sessions_by_model.items():
            is_preferred = model_id in preferred_models
            
            await self._process_model_sessions(
                db, model_id, sessions, is_preferred
            )
        
        # For preferred models with no sessions, open one
        for model_id in preferred_models:
            if model_id not in sessions_by_model:
                auto_logger.info("Preferred model has no sessions, opening one",
                               model_id=model_id,
                               event_type="preferred_model_open_session")
                
                try:
                    await self._open_session_for_model(
                        model_id=model_id,
                        model_name=None,
                        model_type="AUTOMATION",
                    )
                    auto_logger.info("Opened session for preferred model",
                                   model_id=model_id,
                                   event_type="preferred_model_session_opened")
                except Exception as e:
                    auto_logger.error("Failed to open session for preferred model",
                                    model_id=model_id,
                                    error=str(e),
                                    event_type="preferred_model_session_open_error")
    
    async def _process_model_sessions(
        self,
        db: AsyncSession,
        model_id: str,
        sessions: List[RoutedSession],
        is_preferred: bool
    ) -> None:
        """
        Process sessions for a model according to automation rules.
        
        Preferred model rules:
        - If all utilized -> open another session
        - If unutilized_count > 1 -> close one
        - If unutilized_count == 1 -> do nothing
        
        Non-preferred model rules:
        - If any unutilized -> close one
        """
        process_logger = logger.bind(
            model_id=model_id,
            is_preferred=is_preferred,
            session_count=len(sessions)
        )
        
        # Separate utilized and unutilized
        utilized = [s for s in sessions if s.is_utilized]
        unutilized = [s for s in sessions if not s.is_utilized]
        
        # Filter unutilized by idle grace period. Expensive models use their own
        # (longer) grace so an idle session rides to natural on-chain expiry
        # rather than being early-closed — an early close locks the unused stake
        # in userStakesOnHold for ~1 day. Falls back to the global grace when the
        # tier is disabled or the price is unknown.
        idle_grace_seconds = settings.SESSION_IDLE_GRACE_SECONDS
        if await self._is_expensive_model(model_id):
            idle_grace_seconds = settings.SESSION_EXPENSIVE_IDLE_GRACE_SECONDS
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        grace_threshold = now - timedelta(seconds=idle_grace_seconds)
        
        idle_long_enough = [
            s for s in unutilized
            if (s.last_used_at or s.created_at) < grace_threshold
        ]
        
        process_logger.debug("Model session status",
                           utilized_count=len(utilized),
                           unutilized_count=len(unutilized),
                           idle_long_enough=len(idle_long_enough),
                           event_type="model_session_status")
        
        if is_preferred:
            # Preferred model logic
            if len(unutilized) == 0:
                # All utilized - scale up by opening another session
                process_logger.info("All sessions utilized for preferred model, opening another",
                                  event_type="preferred_all_utilized")
                try:
                    await self._open_session_for_model(
                        model_id=model_id,
                        model_name=None,
                        model_type="LLM",
                        user_id=0  # Use fallback private key
                    )
                    process_logger.info("Opened additional session for preferred model",
                                      model_id=model_id,
                                      event_type="preferred_model_scaled_up")
                except Exception as e:
                    process_logger.error("Failed to open additional session for preferred model",
                                       model_id=model_id,
                                       error=str(e),
                                       event_type="preferred_model_scale_up_error")
            elif len(idle_long_enough) > 1:
                # More than one idle - close the most idle
                to_close = max(
                    idle_long_enough,
                    key=lambda s: (now - (s.last_used_at or s.created_at)).total_seconds()
                )
                process_logger.info("Closing excess idle session for preferred model",
                                  session_id=to_close.id,
                                  event_type="preferred_close_excess")
                await self._close_session(db, to_close)
        else:
            # Non-preferred model logic
            if idle_long_enough:
                # Close the most idle session
                to_close = max(
                    idle_long_enough,
                    key=lambda s: (now - (s.last_used_at or s.created_at)).total_seconds()
                )
                process_logger.info("Closing idle session for non-preferred model",
                                  session_id=to_close.id,
                                  event_type="non_preferred_close_idle")
                await self._close_session(db, to_close)
    
    async def _cleanup_expired_sessions(self, db: AsyncSession) -> None:
        """Mark expired sessions and attempt to close them."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        
        result = await db.execute(
            select(RoutedSession)
            .where(
                RoutedSession.state == SessionState.OPEN,
                RoutedSession.expires_at < now
            )
        )
        expired_sessions = result.scalars().all()
        
        for session in expired_sessions:
            logger.info("Closing expired session",
                       session_id=session.id,
                       expired_at=session.expires_at.isoformat(),
                       event_type="closing_expired_session")
            
            # Mark as EXPIRED rather than going through close flow
            session.state = SessionState.EXPIRED
            session.updated_at = now
            
            # Still try to close on proxy router (via the adaptive throttle,
            # shared wallet/nonce with all other on-chain ops).
            try:
                await self._run_onchain(
                    lambda: proxy_router_service.closeSession(session.id),
                    op_name="closeSession",
                    op_logger=logger,
                )

            except Exception as e:
                logger.warning("Error closing expired session on proxy",
                             session_id=session.id,
                             error=str(e),
                             event_type="expired_session_close_error")
        
        if expired_sessions:
            await db.commit()
            logger.info("Cleaned up expired sessions",
                       count=len(expired_sessions),
                       event_type="expired_sessions_cleaned")
    
    # =========================================================================
    # SESSION INFO
    # =========================================================================
    
    async def get_session_info(
        self,
        db: AsyncSession,
        session_id: str
    ) -> Optional[RoutedSession]:
        """Get information about a specific session."""
        result = await db.execute(
            select(RoutedSession).where(RoutedSession.id == session_id)
        )
        return result.scalar_one_or_none()
    
    async def get_model_sessions_summary(
        self,
        db: AsyncSession,
        model_id: str
    ) -> Dict[str, Any]:
        """Get a summary of sessions for a model."""
        sessions = await self._get_open_sessions_for_model(db, model_id)
        
        return {
            "model_id": model_id,
            "total_open": len(sessions),
            "utilized": len([s for s in sessions if s.is_utilized]),
            "unutilized": len([s for s in sessions if not s.is_utilized]),
            "total_active_requests": sum(s.active_requests for s in sessions),
            "sessions": [
                {
                    "id": s.id,
                    "state": s.state.value,
                    "active_requests": s.active_requests,
                    "last_used_at": s.last_used_at.isoformat() if s.last_used_at else None,
                    "expires_at": s.expires_at.isoformat()
                }
                for s in sessions
            ]
        }


# Singleton instance
session_routing_service = SessionRoutingService()

