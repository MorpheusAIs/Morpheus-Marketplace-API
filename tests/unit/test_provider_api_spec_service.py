import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services import provider_api_spec_service as mod  # noqa: E402

VENICE_API = {"stack": "venice", "bindings": {"reasoning.disable": {"kind": "body_param", "param": "venice_parameters.disable_thinking", "paramType": "boolean", "value": True}}}
PROVIDERS = [{"Address": "0xAAAA", "Endpoint": "1.2.3.4:3333"}, {"Address": "0xBBBB", "Endpoint": "5.6.7.8:3333"}]


def _svc(now):
    return mod.ProviderApiSpecService(ttl_seconds=600, negative_ttl_seconds=60, clock=lambda: now[0])


async def test_get_spec_resolves_endpoint_then_pings_and_caches():
    now = [1000.0]
    svc = _svc(now)
    ping = {"models": [{"modelId": "0x01", "api": VENICE_API}, {"modelId": "0x02"}]}
    with patch.object(mod.proxy_router_service, "getProviders", new_callable=AsyncMock, return_value=PROVIDERS) as gp, \
         patch.object(mod.proxy_router_service, "pingProvider", new_callable=AsyncMock, return_value=ping) as pp:
        spec = await svc.get_spec("0xaaaa", "0x01")  # lowercase address must still match
        assert spec == VENICE_API
        pp.assert_awaited_once_with("0xAAAA", "1.2.3.4:3333")

        again = await svc.get_spec("0xaaaa", "0x01")
        assert again == VENICE_API
        assert pp.await_count == 1, "second lookup must be served from cache"
        assert gp.await_count == 1

        # a different model on the same provider: no new ping either (report cached per provider)
        assert await svc.get_spec("0xaaaa", "0x02") is None
        assert pp.await_count == 1


async def test_get_spec_expires_after_ttl():
    now = [1000.0]
    svc = _svc(now)
    ping = {"models": [{"modelId": "0x01", "api": VENICE_API}]}
    with patch.object(mod.proxy_router_service, "getProviders", new_callable=AsyncMock, return_value=PROVIDERS), \
         patch.object(mod.proxy_router_service, "pingProvider", new_callable=AsyncMock, return_value=ping) as pp:
        await svc.get_spec("0xAAAA", "0x01")
        now[0] += 601
        await svc.get_spec("0xAAAA", "0x01")
        assert pp.await_count == 2


async def test_get_spec_negative_caches_failures():
    now = [1000.0]
    svc = _svc(now)
    with patch.object(mod.proxy_router_service, "getProviders", new_callable=AsyncMock, return_value=PROVIDERS), \
         patch.object(mod.proxy_router_service, "pingProvider", new_callable=AsyncMock, side_effect=RuntimeError("down")) as pp:
        assert await svc.get_spec("0xAAAA", "0x01") is None
        assert await svc.get_spec("0xAAAA", "0x01") is None
        assert pp.await_count == 1, "failure must be negative-cached"
        now[0] += 61
        assert await svc.get_spec("0xAAAA", "0x01") is None
        assert pp.await_count == 2


async def test_get_spec_unknown_provider_or_missing_inputs():
    now = [1000.0]
    svc = _svc(now)
    with patch.object(mod.proxy_router_service, "getProviders", new_callable=AsyncMock, return_value=PROVIDERS), \
         patch.object(mod.proxy_router_service, "pingProvider", new_callable=AsyncMock) as pp:
        assert await svc.get_spec("0xCCCC", "0x01") is None  # not in providers list
        assert await svc.get_spec("", "0x01") is None
        assert await svc.get_spec("0xAAAA", "") is None
        pp.assert_not_awaited()
