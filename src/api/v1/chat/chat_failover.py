"""
Gateway-owned provider failover.

Failover = open a new session with a DIFFERENT provider. It applies ONLY
when the provider becomes unavailable during a session (conn refused /
timeouts / 5xx / provider death). On such a failure the gateway marks the
dead RoutedSession FAILED, opens a fresh session for the same model (the
proxy-router's per-bid handshake skips the dead provider), and the caller
retries the prompt exactly once.

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


def is_failover_eligible(exc: proxy_router_service.ProxyRouterServiceError) -> bool:
    """Decide whether a proxy error means the provider is unavailable.

    Eligible: provider unreachable / 5xx / timeouts / transport failures.
    Not eligible: session expired/not found (separate renewal mechanism),
    user/4xx errors, TEE failures, client-cancelled requests.
    """
    if not settings.CHAT_FAILOVER_ENABLED:
        return False

    message = str(exc).lower()
    if any(p in message for p in NON_FAILOVER_ERROR_PATTERNS):
        return False

    status = exc.status_code
    if status is not None and 400 <= status < 500:
        return False
    if status is not None and status >= 500:
        return True
    # No status: gateway<->proxy transport failure, or wrapped errors.
    if exc.error_type == "network_error":
        return True
    return any(p in message for p in PROVIDER_FAILURE_PATTERNS)
