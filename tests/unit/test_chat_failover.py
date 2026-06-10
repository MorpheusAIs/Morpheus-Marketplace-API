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
         patch.object(chat_failover.session_routing_service, "invalidate_session",
                      new_callable=AsyncMock, return_value=True) as invalidate, \
         patch.object(chat_failover.session_routing_service, "route_request",
                      new_callable=AsyncMock, return_value="0xnew") as route, \
         patch.object(chat_failover.proxy_router_service, "getRatedBids",
                      new_callable=AsyncMock, return_value=_rated_bids_response(2)) as bids, \
         patch.object(chat_failover.asyncio, "sleep", new_callable=AsyncMock):
        yield {"invalidate": invalidate, "route": route, "bids": bids, "user": mock_user}


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

    async def test_single_bid_blocks_retry_but_still_invalidates(self, failover_mocks):
        failover_mocks["bids"].return_value = _rated_bids_response(1)
        new_id = await _failover(failover_mocks)
        assert new_id is None
        failover_mocks["invalidate"].assert_awaited_once()
        failover_mocks["route"].assert_not_awaited()

    async def test_bids_lookup_failure_proceeds_optimistically(self, failover_mocks):
        failover_mocks["bids"].side_effect = Exception("chain read failed")
        new_id = await _failover(failover_mocks)
        assert new_id == "0xnew"

    async def test_route_failure_returns_none(self, failover_mocks):
        failover_mocks["route"].side_effect = Exception("no bids left")
        new_id = await _failover(failover_mocks)
        assert new_id is None
