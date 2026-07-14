"""Tests for gateway-owned provider failover policy."""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services.proxy_router_service import ProxyRouterServiceError
from src.api.v1.chat import chat_failover


def _err(message, status_code=None, error_type="unknown"):
    return ProxyRouterServiceError(message, status_code=status_code, error_type=error_type)


class TestIsFailoverEligible:
    @pytest.mark.parametrize("exc", [
        _err('HTTP 500: {"error":"provider request failed: failed to connect to provider: dial tcp 1.2.3.4:8083: connect: connection refused"}', 500, "server_error"),
        _err('HTTP 500: {"error":"provider request failed: read timed out after 3 retries"}', 500, "server_error"),
        _err('HTTP 500: {"error":"provider request failed: provider closed connection without sending any data"}', 500, "server_error"),
        _err('HTTP 500: {"error":"provider not found"}', 500, "server_error"),
        _err('HTTP 502: {"error":"bad gateway"}', 502, "server_error"),
        _err("Request failed after 1 attempts: All connection attempts failed", None, "network_error"),
        # Streaming transport failure: chatCompletionsStream's catch-all wrap
        # (proxy_router_service.py:786-792) produces status=None, type="unknown".
        _err("Failed to create chat completions stream: All connection attempts failed", None, "unknown"),
    ])
    def test_provider_unavailability_is_eligible(self, exc):
        assert chat_failover.is_failover_eligible(exc) is True

    @pytest.mark.parametrize("exc", [
        # Impaired provider: model backend rate-limited / out of capacity.
        # Newer proxy-routers propagate the real upstream status (429/503).
        _err('HTTP 429: {"providerModelError":{"error":{"message":"Rate limit exceeded","code":"rate_limit_exceeded"}},"upstreamStatusCode":429}', 429, "rate_limit_error"),
        _err('HTTP 503: {"providerModelError":{"error":{"message":"Model is currently overloaded"}},"upstreamStatusCode":503}', 503, "server_error"),
        # Older proxy-routers collapse backend errors into HTTP 400 with a
        # providerModelError body; the pattern fallback must catch these.
        _err('HTTP 400: {"providerModelError":{"error":{"message":"Rate limit exceeded, please try again later"}}}', 400, "client_error"),
        _err('HTTP 400: {"providerModelError":{"error":{"message":"Too Many Requests"}}}', 400, "client_error"),
        _err('HTTP 400: {"providerModelError":{"error":{"message":"The model is at capacity"}}}', 400, "client_error"),
        _err('HTTP 400: {"providerModelError":{"error":{"message":"model unavailable"}}}', 400, "client_error"),
        # Newer provider behind an older consumer node: upstreamStatusCode is
        # embedded in the body even though the HTTP status is still 400.
        _err('HTTP 400: {"providerModelError":{"error":"busy"},"upstreamStatusCode":429}', 400, "client_error"),
        # Provider-backend config failure (e.g. provider's Venice key is bad):
        # the gateway remaps 401/403/404 + providerModelError to 502
        # provider_error, which is failover-eligible — another bid may be
        # configured correctly.
        _err('HTTP 502: {"providerModelError":{"error":"Authentication failed"},"upstreamStatusCode":401}', 502, "provider_error"),
    ])
    def test_impaired_provider_is_eligible(self, exc):
        assert chat_failover.is_failover_eligible(exc) is True

    @pytest.mark.parametrize("exc", [
        # Session-state errors belong to the SEPARATE renewal mechanism —
        # failover must NOT trigger even though they arrive as 500.
        _err('HTTP 500: {"error":"session expired"}', 500, "server_error"),
        _err('HTTP 500: {"error":"session not found"}', 500, "server_error"),
        # User/4xx and known-permanent failures.
        _err('HTTP 400: {"error":"invalid request"}', 400, "client_error"),
        _err('HTTP 404: {"error":"not found"}', 404, "client_error"),
        _err('HTTP 500: {"error":"p-node tee attestation failed: quote invalid"}', 500, "server_error"),
        _err('HTTP 500: {"error":"llm tee verification failed"}', 500, "server_error"),
        _err('HTTP 500: {"error":"request cancelled while waiting in queue: context canceled"}', 500, "server_error"),
        _err("something odd with no status and no known pattern", None, "unknown"),
        # Genuine provider-side client errors (backend reached, request/config
        # bad) must NOT fail over even though they carry providerModelError.
        _err('HTTP 400: {"providerModelError":{"error":"Authentication failed"}}', 400, "client_error"),
        _err('HTTP 400: {"providerModelError":{"error":{"message":"invalid request body"}}}', 400, "client_error"),
        # Capacity-like words WITHOUT a providerModelError body stay ineligible.
        _err('HTTP 400: {"error":"rate limit exceeded"}', 400, "client_error"),
    ])
    def test_not_eligible(self, exc):
        assert chat_failover.is_failover_eligible(exc) is False

    def test_kill_switch_disables_failover(self):
        exc = _err('HTTP 500: {"error":"provider request failed"}', 500, "server_error")
        with patch.object(chat_failover.settings, "CHAT_FAILOVER_ENABLED", False):
            assert chat_failover.is_failover_eligible(exc) is False


def _rated_bids_response(n):
    resp = MagicMock()
    resp.json.return_value = [{"bid": {"provider": f"0xp{i}"}} for i in range(n)]
    return resp


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = 42
    return user


FAILED_PROVIDER = "0xAbC1234567890abcdef1234567890abcdef12345"


def _session_row(provider_address=FAILED_PROVIDER):
    row = MagicMock()
    row.provider_address = provider_address
    return row


@pytest.fixture
def failover_mocks(mock_user):
    """Patch DB, routing service and bids lookup used by attempt_failover."""
    db = AsyncMock()

    class FakeGetDb:
        async def __aenter__(self):
            return db
        async def __aexit__(self, *args):
            return False

    with patch.object(chat_failover, "get_db", lambda: FakeGetDb()), \
         patch.object(chat_failover.session_routing_service, "get_session_info",
                      new_callable=AsyncMock, return_value=_session_row()) as session_info, \
         patch.object(chat_failover.session_routing_service, "invalidate_session",
                      new_callable=AsyncMock, return_value=True) as invalidate, \
         patch.object(chat_failover.session_routing_service, "route_request",
                      new_callable=AsyncMock, return_value="0xnew") as route, \
         patch.object(chat_failover.proxy_router_service, "getRatedBids",
                      new_callable=AsyncMock, return_value=_rated_bids_response(2)) as bids, \
         patch.object(chat_failover.asyncio, "sleep", new_callable=AsyncMock):
        yield {"session_info": session_info, "invalidate": invalidate,
               "route": route, "bids": bids, "user": mock_user}


def _failover(mocks, **overrides):
    kwargs = dict(
        original_session_id="0xdead",
        model_id="0xmodel",
        requested_model="llama-3.3-70b",
        user=mocks["user"],
        logger=MagicMock(),
        failure_reason="connection refused",
    )
    kwargs.update(overrides)
    return chat_failover.attempt_failover(**kwargs)


class TestAttemptFailover:
    async def test_happy_path_returns_new_session(self, failover_mocks):
        new_id = await _failover(failover_mocks)
        assert new_id == "0xnew"
        failover_mocks["invalidate"].assert_awaited_once()
        failover_mocks["bids"].assert_awaited_once()
        failover_mocks["route"].assert_awaited_once()

    async def test_reroute_excludes_failed_provider(self, failover_mocks):
        """The failed session's provider is passed as omit_provider so the
        retry can't land on the same impaired (but reachable) provider."""
        await _failover(failover_mocks)
        assert failover_mocks["route"].await_args.kwargs["omit_provider"] == FAILED_PROVIDER

    async def test_unknown_provider_reroutes_without_exclusion(self, failover_mocks):
        """Legacy rows without a stored provider still fail over (omit=None)."""
        failover_mocks["session_info"].return_value = _session_row(provider_address=None)
        new_id = await _failover(failover_mocks)
        assert new_id == "0xnew"
        assert failover_mocks["route"].await_args.kwargs["omit_provider"] is None

    async def test_missing_session_row_reroutes_without_exclusion(self, failover_mocks):
        failover_mocks["session_info"].return_value = None
        new_id = await _failover(failover_mocks)
        assert new_id == "0xnew"
        assert failover_mocks["route"].await_args.kwargs["omit_provider"] is None

    async def test_single_bid_skips_invalidate_and_retry(self, failover_mocks):
        # Single-bid: nothing to fail over to. The session must be left OPEN
        # (NOT invalidated/early-closed) so it rides to natural expiry and the
        # user's MOR is not locked; no retry is attempted.
        failover_mocks["bids"].return_value = _rated_bids_response(1)
        new_id = await _failover(failover_mocks)
        assert new_id is None
        failover_mocks["invalidate"].assert_not_awaited()
        failover_mocks["route"].assert_not_awaited()

    async def test_bids_lookup_failure_proceeds_optimistically(self, failover_mocks):
        failover_mocks["bids"].side_effect = Exception("chain read failed")
        new_id = await _failover(failover_mocks)
        assert new_id == "0xnew"

    async def test_route_failure_returns_none(self, failover_mocks):
        failover_mocks["route"].side_effect = Exception("no bids left")
        new_id = await _failover(failover_mocks)
        assert new_id is None

    async def test_invalidate_failure_returns_none_without_routing(self, failover_mocks):
        failover_mocks["invalidate"].side_effect = Exception("db teardown failed")
        new_id = await _failover(failover_mocks)
        assert new_id is None
        failover_mocks["route"].assert_not_awaited()
