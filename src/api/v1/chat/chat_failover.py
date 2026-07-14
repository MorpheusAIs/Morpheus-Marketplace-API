"""
Gateway-owned provider failover.

Failover = open a new session with a DIFFERENT provider. It applies ONLY
when the provider becomes unavailable during a session (conn refused /
timeouts / 5xx / provider death) AND the model has an alternate bid to fail
over to. On such a failure the gateway marks the dead RoutedSession FAILED,
opens a fresh session for the same model (the proxy-router's per-bid
handshake skips the dead provider), and the caller retries the prompt
exactly once.

Single-bid models are left untouched: with no alternate bid there is nothing
to fail over to, so the session is NOT invalidated or closed early (which
would lock the user's MOR for ~1 day). It rides to natural expiry and the
original error is surfaced — the pre-failover behavior. The alternate-bid
check therefore runs BEFORE any invalidation.

Session expiry ("session expired") is a SEPARATE mechanism — the renewal
flow in the chat handlers — and is explicitly EXCLUDED here.

Proxy-side failover must stay OFF (openSession failover=False): if the
c-node closed/reopened sessions itself, its sessionID would diverge from
our routed_sessions DB.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from ....core.config import settings
from ....db.database import get_db
from ....services import proxy_router_service
from ....services.session_routing_service import session_routing_service

# Failures that must NEVER trigger failover:
# - session-state errors: handled by the separate renewal mechanism
# - TEE failures (NON_RETRIABLE_ERROR_PATTERNS): permanent
# - queue cancellation: the client went away
NON_FAILOVER_ERROR_PATTERNS = [
    "session expired",
    "session not found",
    *proxy_router_service.NON_RETRIABLE_ERROR_PATTERNS,
    "request cancelled while waiting in queue",
]

# Provider-unavailability signatures from the proxy-router (see
# proxy-router/internal/proxyapi/proxy_sender.go sentinel errors).
# Used when no status code is available on the exception.
PROVIDER_FAILURE_PATTERNS = [
    "failed to connect to provider",
    "failed to write to provider",
    "provider request failed",
    "provider closed connection",
    "provider not found",
    "read timed out",
    # chatCompletionsStream wraps transport errors with status=None and
    # error_type="unknown" (proxy_router_service.py:786-792). Mid-stream
    # wraps match too, but those are excluded by the chunk_count==0 gate.
    "failed to create chat completions stream",
]

# Impaired-provider signatures inside a providerModelError body: the provider
# is reachable but its model backend (Venice etc.) is rate-limited or out of
# capacity. Newer proxy-routers surface these with the real upstream status
# (429/503); older ones collapse them into HTTP 400, so these patterns are
# the fallback for recognizing them in the embedded error text.
IMPAIRED_PROVIDER_PATTERNS = [
    "rate limit",
    "rate_limit",
    "too many requests",
    "capacity",
    "overloaded",
    "model unavailable",
    "model is unavailable",
    "service unavailable",
    # upstreamStatusCode emitted by newer providers but collapsed to HTTP 400
    # by an older consumer node in between.
    '"upstreamstatuscode":429',
    '"upstreamstatuscode": 429',
    '"upstreamstatuscode":503',
    '"upstreamstatuscode": 503',
]


def _is_impaired_provider_error(message: str) -> bool:
    """True for providerModelError bodies that signal rate-limit/capacity.

    Only applies to provider model errors (backend reached, backend degraded);
    other 4xx bodies (auth failed, malformed request) stay ineligible.
    """
    if "providermodelerror" not in message:
        return False
    return any(p in message for p in IMPAIRED_PROVIDER_PATTERNS)


def is_failover_eligible(exc: proxy_router_service.ProxyRouterServiceError) -> bool:
    """Decide whether a proxy error means the provider is unavailable/impaired.

    Eligible: provider unreachable / 5xx / timeouts / transport failures /
    rate-limited or out-of-capacity model backends (429, or capacity patterns
    inside a providerModelError body).
    Not eligible: session expired/not found (separate renewal mechanism),
    genuine user/4xx errors, TEE failures, client-cancelled requests.
    """
    if not settings.CHAT_FAILOVER_ENABLED:
        return False

    message = str(exc).lower()
    if any(p in message for p in NON_FAILOVER_ERROR_PATTERNS):
        return False

    status = exc.status_code
    if status == 429:
        # Provider's model backend is rate-limited — treat as impaired.
        return True
    if status is not None and 400 <= status < 500:
        # Older proxy-routers collapse backend 429/503 into HTTP 400 with a
        # providerModelError body; recognize those by pattern as a fallback.
        return _is_impaired_provider_error(message)
    if status is not None and status >= 500:
        return True
    # No status: gateway<->proxy transport failure, or wrapped errors.
    if exc.error_type == "network_error":
        return True
    return any(p in message for p in PROVIDER_FAILURE_PATTERNS)


async def _release_new_session_quiet(session_id: str, logger) -> None:
    """Release a just-assigned session's request slot, logging failures."""
    try:
        async with get_db() as db:
            await session_routing_service.release_session(db, session_id)
    except Exception as release_err:
        logger.warning("Failed to release abandoned failover session",
                       session_id=session_id,
                       error=str(release_err),
                       event_type="session_release_error")


async def _has_alternate_bids(model_id: str, logger) -> bool:
    """True if the model has more than one rated bid.

    On lookup failure we proceed optimistically: route_request will fail
    with a clean error anyway if no healthy bid exists.
    """
    try:
        response = await proxy_router_service.getRatedBids(model_id)
        result = response.json()
        if isinstance(result, list):
            count = len(result)
        elif isinstance(result, dict):
            count = len(result.get("bids", []))
        else:
            count = 0
        logger.info("Failover bid availability check",
                    model_id=model_id,
                    bid_count=count,
                    event_type="failover_bid_check")
        return count > 1
    except Exception as e:
        logger.warning("Failover bid check failed, proceeding with retry",
                       model_id=model_id,
                       error=str(e),
                       event_type="failover_bid_check_error")
        return True


async def attempt_failover(
    *,
    original_session_id: str,
    model_id: Optional[str],
    requested_model: Optional[str],
    user,
    logger,
    request_id: Optional[str] = None,
    failure_reason: str = "",
) -> Optional[str]:
    """
    Gateway-owned provider failover: when a model has an ALTERNATE bid, mark
    the dead session FAILED (with a background on-chain close) and route to a
    fresh session for the same model — the proxy-router's per-bid handshake
    lands it on a surviving provider.

    Single-bid guardrail (checked FIRST): if the model has no alternate bid,
    there is nowhere to fail over to. We do NOT invalidate or early-close the
    session — that would lock the user's MOR for ~1 day (an early on-chain
    close pushes the unused stake into userStakesOnHold) and force a fresh,
    equally-dead session on the next prompt. Instead we leave the session
    OPEN so it rides to its natural expiry (stake returns, no lock) and
    surface the original error to the caller — the pre-failover behavior.

    Returns the new session id (already assigned to this request), or None
    when failover is not possible (caller surfaces the original error).

    Session-accounting contract: this function does NOT release or assign
    the ORIGINAL session (its release stays with index.py's finally); the
    NEW session is assigned by route_request and must be released by the
    code that performs the retry.
    """
    failover_logger = logger.bind(
        original_session_id=original_session_id,
        model_id=model_id,
        request_id=request_id,
    )

    # 1. Single-bid guardrail FIRST. With no alternate bid there is nothing to
    #    fail over to, so do nothing destructive: leave the session OPEN to
    #    expire naturally (no early on-chain close, no MOR lock). Must run
    #    BEFORE invalidate so single-bid models don't pay the early-close cost.
    if model_id and not await _has_alternate_bids(model_id, failover_logger):
        failover_logger.warning(
            "No alternate bid available; leaving session to expire naturally",
            failure_reason=failure_reason[:300],
            event_type="failover_no_alternate_bids")
        return None

    failover_logger.warning("Attempting gateway failover to a new provider",
                            failure_reason=failure_reason[:300],
                            event_type="failover_triggered")

    # 2. A sibling bid exists and is worth trying. Look up which provider
    #    served the dead session (so the reroute can exclude it), then unpin
    #    the session so no request routes to it again (marks FAILED +
    #    best-effort background on-chain close).
    failed_provider: Optional[str] = None
    try:
        async with get_db() as db:
            session_row = await session_routing_service.get_session_info(
                db, original_session_id
            )
            if session_row is not None:
                failed_provider = session_row.provider_address
            await session_routing_service.invalidate_session(
                db, original_session_id, failure_reason[:300]
            )
    except Exception as e:
        failover_logger.error("Failover invalidation failed",
                              error=str(e),
                              event_type="failover_invalidate_failed")
        return None

    # 3. Open/route a fresh session by model, excluding the failed provider
    #    (omitProvider in the proxy-router's bid selection + skipping its
    #    sibling idle sessions). Crucial for IMPAIRED providers: they pass the
    #    session-open handshake (TCP is fine, only the backend is broken), so
    #    without the exclusion the top-rated — broken — bid would be picked
    #    again. Transport-dead providers were already skipped by the handshake.
    try:
        new_session_id = await session_routing_service.route_request(
            user_id=user.id,
            requested_model=requested_model,
            model_type="LLM",
            omit_provider=failed_provider,
        )
    except Exception as e:
        failover_logger.error("Failover rerouting failed",
                              error=str(e),
                              event_type="failover_route_failed")
        return None

    if not new_session_id:
        return None

    failover_logger.info("Failover routed to new session",
                         new_session_id=new_session_id,
                         event_type="failover_new_session")
    # Brief delay so the freshly opened session is registered everywhere
    # (same as the renewal path).
    try:
        await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        # Client disconnected before the retry took ownership — release the
        # just-assigned session so its request slot doesn't leak. Shielded
        # so a second cancellation can't abandon the release mid-flight.
        try:
            await asyncio.shield(_release_new_session_quiet(new_session_id, failover_logger))
        except asyncio.CancelledError:
            pass
        raise
    return new_session_id
