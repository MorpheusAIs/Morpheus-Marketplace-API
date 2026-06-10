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
