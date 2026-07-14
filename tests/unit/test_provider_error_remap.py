"""Tests for remapping provider-backend config errors (401/403/404) to 502.

A providerModelError with one of these statuses means the PROVIDER's own
credentials/config for its model backend are broken — not the gateway
client's fault. Passing e.g. a 401 through would make OpenAI SDK clients
believe their gateway API key is invalid.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services.proxy_router_service import remap_provider_upstream_error

PROVIDER_AUTH_BODY = '{"providerModelError":{"error":"Authentication failed"},"statusCode":401}'


@pytest.mark.parametrize("status", [401, 403, 404])
def test_provider_config_statuses_remap_to_502(status):
    assert remap_provider_upstream_error(status, PROVIDER_AUTH_BODY) == (502, "provider_error")


@pytest.mark.parametrize("status,body", [
    # 429 must pass through untouched so clients back off properly.
    (429, '{"providerModelError":{"error":{"message":"Rate limit exceeded"}},"statusCode":429}'),
    # 400/422: the client's request may genuinely be malformed.
    (400, '{"providerModelError":{"error":{"message":"invalid request body"}}}'),
    (422, '{"providerModelError":{"error":{"message":"unsupported parameter"}}}'),
    # 5xx already surfaces as server_error / failover.
    (503, '{"providerModelError":{"error":"overloaded"},"statusCode":503}'),
    # 401 WITHOUT a providerModelError body is a real auth error between the
    # gateway and the proxy-router — must not be masked.
    (401, '{"error":"unauthorized"}'),
    (404, '{"error":"not found"}'),
])
def test_non_provider_config_errors_are_not_remapped(status, body):
    assert remap_provider_upstream_error(status, body) is None
