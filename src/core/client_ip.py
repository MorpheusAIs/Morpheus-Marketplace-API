"""
Resolve the caller's address and user agent for a request.

Shared by the signup-bonus per-address guard (billing) and API key request
logging (dependencies), so both see the same address for the same request.
"""
import ipaddress

from fastapi import Request

# Longest user agent written to a log line. The header is caller-controlled.
MAX_USER_AGENT_LENGTH = 200


def resolve_client_ip(request: Request) -> str:
    """Resolve the client IP without trusting caller-controlled headers.

    The first X-Forwarded-For hop is whatever the client sent, so it must never
    feed abuse controls or audit logs. Instead:

    - Only consult X-Forwarded-For when the direct peer is a private/loopback
      address, i.e. the request actually came through our load balancer.
    - Use the RIGHTMOST hop, which was appended by the trusted proxy itself;
      anything a client prepends gets pushed left and ignored.
    - Otherwise (direct connection), use the socket peer address.
    """
    peer_ip = request.client.host if request.client else ""

    peer_is_trusted_proxy = False
    if peer_ip:
        try:
            addr = ipaddress.ip_address(peer_ip)
            peer_is_trusted_proxy = addr.is_private or addr.is_loopback
        except ValueError:
            pass

    if peer_is_trusted_proxy:
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
            if hops:
                return hops[-1]

    return peer_ip or "unknown"


def resolve_user_agent(request: Request) -> str:
    """The User-Agent header, truncated, or an empty string when absent."""
    return request.headers.get("User-Agent", "")[:MAX_USER_AGENT_LENGTH]
