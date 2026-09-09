import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services import proxy_router_service  # noqa: E402


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


async def test_get_providers_returns_list():
    with patch.object(proxy_router_service, "_execute_request", new_callable=AsyncMock) as ex:
        ex.return_value = _Resp({"providers": [{"Address": "0xAbC", "Endpoint": "1.2.3.4:3333", "IsDeleted": False}]})
        providers = await proxy_router_service.getProviders()
    assert providers == [{"Address": "0xAbC", "Endpoint": "1.2.3.4:3333", "IsDeleted": False}]
    called = ex.await_args
    assert "blockchain/providers" in str(called.args)


async def test_ping_provider_posts_body_and_returns_models():
    with patch.object(proxy_router_service, "_execute_request", new_callable=AsyncMock) as ex:
        ex.return_value = _Resp({"ping": 12, "version": "7.11", "models": [{"modelId": "0x01", "api": {"stack": "venice"}}]})
        res = await proxy_router_service.pingProvider("0xabc", "1.2.3.4:3333")
    assert res["models"][0]["api"]["stack"] == "venice"
    called = ex.await_args
    assert "proxy/provider/ping" in str(called.args)
    body = called.kwargs.get("json_data")
    assert body == {"providerAddr": "0xabc", "providerUrl": "1.2.3.4:3333"}
