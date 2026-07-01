"""Nonce errors must be non-retriable in _execute_request.

Re-sending the identical request never fixes a nonce race, and blind backoff
retries amplify a failover storm. Surfacing the nonce error after one attempt
lets the gateway's adaptive wallet throttle engage (see
SessionRoutingService._run_onchain). Non-nonce 5xx errors must still be retried.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services import proxy_router_service as prs


def _fake_client_returning(status: int, body: str):
    req = httpx.Request("POST", "http://proxy/blockchain/models/0xabc/session")
    resp = httpx.Response(status, text=body, request=req)  # .raise_for_status() raises on 5xx
    client = MagicMock()
    client.request = AsyncMock(return_value=resp)
    return client


async def test_nonce_error_is_not_retried():
    client = _fake_client_returning(500, '{"error":"failed to send transaction: replacement transaction underpriced"}')
    with patch.object(prs, "get_http_client", new_callable=AsyncMock, return_value=client):
        with pytest.raises(prs.ProxyRouterServiceError):
            await prs.openSession(target_model="0xabc", session_duration=60)
    assert client.request.await_count == 1, "nonce errors must surface after one attempt"


async def test_non_nonce_5xx_is_still_retried():
    client = _fake_client_returning(500, '{"error":"no provider accepting session"}')
    with patch.object(prs, "get_http_client", new_callable=AsyncMock, return_value=client), \
         patch.object(prs.asyncio, "sleep", new_callable=AsyncMock):
        with pytest.raises(prs.ProxyRouterServiceError):
            await prs.openSession(target_model="0xabc", session_duration=60)
    assert client.request.await_count == 3, "non-nonce 5xx must still exhaust max_retries"
