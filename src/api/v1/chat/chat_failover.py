"""
Gateway-owned provider failover.

Failover = open a new session with a DIFFERENT provider. It applies ONLY
when the provider becomes UNREACHABLE during a session — i.e. a transport
failure (conn refused / write failure / provider closed the connection /
provider-task gone / read timeout / LB 502-504). On such a failure the
gateway marks the dead RoutedSession FAILED, opens a fresh session for the
same model (the proxy-router's per-bid handshake skips the dead provider),
and the caller retries the prompt exactly once.

It deliberately does NOT trigger on errors where the provider ANSWERED with
an application/config error (e.g. "api adapter not found", an upstream EOF
mid-prompt). Those mean the bid is reachable but misconfigured/broken;
retrying them on a fresh session cannot fix the model and previously caused
an open/fail/early-close reopen storm (see narrowing rationale below).

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

# Transport-level signatures meaning a node became UNREACHABLE — the only
# class of failure that warrants opening a new session on a different bid.
#
# These are the proxy-router's provider-transport sentinels (see
# proxy-router/internal/proxyapi/proxy_sender.go) plus the gateway<->proxy
# stream-setup transport wrapper. A provider that ANSWERS with an
# application/config error (e.g. "api adapter not found", or an upstream
# "...EOF" mid-prompt) is reachable and is NOT listed here on purpose:
# failover cannot fix a broken/misconfigured bid, and treating those as
# provider-unavailability caused a reopen storm (open -> immediate fail ->
# early on-chain close (MOR lock) -> reopen) that amplified session volume.
#
# NB: the generic envelope "provider request failed: ..." was REMOVED — the
# proxy-router wraps EVERY provider error in it (including adapter-not-found
# and upstream EOF), so it matched far more than genuine provider death.
PROVIDER_TRANSPORT_PATTERNS = [
    "failed to connect to provider",
    "failed to write to provider",
    "provider closed connection",
    "provider not found",
    "read timed out",
    # chatCompletionsStream wraps gateway<->proxy transport failures with
    # status=None ("Failed to create chat completions stream: <transport
    # error>"). App errors arrive as ProxyRouterServiceError with a status and
    # are re-raised before this wrap, so this only catches real transport.
    "failed to create chat completions stream",
]


def is_failover_eligible(exc: proxy_router_service.ProxyRouterServiceError) -> bool:
    """Decide whether a proxy error means the provider became UNREACHABLE.

    Eligible (transport failure -> a different bid may work):
      - LB infra errors (502/503/504): the node could not be reached.
      - gateway<->proxy transport failure (error_type == "network_error").
      - proxy-router provider-transport sentinels (PROVIDER_TRANSPORT_PATTERNS).

    NOT eligible:
      - session expired/not found (handled by the separate renewal mechanism),
      - user/4xx errors, TEE failures, client-cancelled requests,
      - a bare 5xx whose body is an application/config error (e.g.
        "api adapter not found", upstream "EOF") — the bid is reachable but
        broken; failover just churns sessions and locks MOR.
    """
    if not settings.CHAT_FAILOVER_ENABLED:
        return False

    message = str(exc).lower()
    if any(p in message for p in NON_FAILOVER_ERROR_PATTERNS):
        return False

    status = exc.status_code
    # Caller/config errors (4xx) never warrant failover.
    if status is not None and 400 <= status < 500:
        return False
    # Infra-level gateway errors: the load balancer could not reach the node
    # (e.g. a provider/proxy task cycling during a deploy) — transport failure.
    if status in (502, 503, 504):
        return True
    # No/other status: gateway<->proxy transport failure.
    if exc.error_type == "network_error":
        return True
    # A bare 5xx is NOT sufficient on its own; require a transport signature.
    # The provider may have ANSWERED with an application error, which failover
    # cannot fix.
    return any(p in message for p in PROVIDER_TRANSPORT_PATTERNS)


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
    Gateway-owned provider failover: mark the dead session FAILED (with a
    background on-chain close), verify the model has an alternate bid,
    and route to a fresh session for the same model — the proxy-router's
    per-bid handshake lands it on a surviving provider.

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
    failover_logger.warning("Attempting gateway failover to a new provider",
                            failure_reason=failure_reason[:300],
                            event_type="failover_triggered")

    # 1. Unpin the dead session so no request routes to it again.
    #    Do this even if we end up unable to retry.
    try:
        async with get_db() as db:
            await session_routing_service.invalidate_session(
                db, original_session_id, failure_reason[:300]
            )
    except Exception as e:
        failover_logger.error("Failover invalidation failed",
                              error=str(e),
                              event_type="failover_invalidate_failed")
        return None

    # 2. Only retry when the model has >1 bid (spec guardrail).
    if model_id and not await _has_alternate_bids(model_id, failover_logger):
        failover_logger.warning("No alternate bid available, not retrying",
                                event_type="failover_no_alternate_bids")
        return None

    # 3. Open/route a fresh session by model. The proxy-router tries bids
    #    best-first with a provider handshake per bid, so the dead
    #    provider is skipped automatically.
    try:
        new_session_id = await session_routing_service.route_request(
            user_id=user.id,
            requested_model=requested_model,
            model_type="LLM",
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
