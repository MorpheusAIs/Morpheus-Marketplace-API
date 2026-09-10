import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services import provider_api_spec_service as mod  # noqa: E402

VENICE_API = {"stack": "venice", "bindings": {"reasoning.disable": {"kind": "body_param", "param": "venice_parameters.disable_thinking", "paramType": "boolean", "value": True}}}
PROVIDERS = [
    {"Address": "0xAAAA", "Endpoint": "1.2.3.4:3333"},
    {"Address": "0xBBBB", "Endpoint": "5.6.7.8:3333"},
    {"Address": "0xDEAD", "Endpoint": "9.9.9.9:3333", "IsDeleted": True},
]


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

        # a deleted provider must never resolve to an endpoint (and so is never pinged)
        assert await svc.get_spec("0xdead", "0x01") is None
        assert pp.await_count == 1, "deleted provider must never be pinged"


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


async def test_concurrent_lookups_for_same_provider_ping_once():
    now = [1000.0]
    svc = _svc(now)
    ping = {"models": [{"modelId": "0x01", "api": VENICE_API}]}
    release = asyncio.Event()

    async def slow_ping(*args, **kwargs):
        await release.wait()
        return ping

    with patch.object(mod.proxy_router_service, "getProviders", new_callable=AsyncMock, return_value=PROVIDERS), \
         patch.object(mod.proxy_router_service, "pingProvider", new_callable=AsyncMock, side_effect=slow_ping) as pp:
        t1 = asyncio.create_task(svc.get_spec("0xaaaa", "0x01"))
        t2 = asyncio.create_task(svc.get_spec("0xaaaa", "0x01"))
        await asyncio.sleep(0.05)  # let both tasks reach the ping call
        release.set()
        spec1, spec2 = await asyncio.gather(t1, t2)
        assert spec1 == VENICE_API
        assert spec2 == VENICE_API
        assert pp.await_count == 1, "concurrent lookups for the same provider must ping once"


async def test_slow_provider_does_not_block_cached_lookup():
    now = [1000.0]
    svc = _svc(now)
    ping_b = {"models": [{"modelId": "0x01", "api": VENICE_API}]}
    block = asyncio.Event()

    async def routed_ping(address, endpoint):
        if address == "0xAAAA":
            await block.wait()
            return {"models": [{"modelId": "0x01", "api": VENICE_API}]}
        return ping_b

    with patch.object(mod.proxy_router_service, "getProviders", new_callable=AsyncMock, return_value=PROVIDERS), \
         patch.object(mod.proxy_router_service, "pingProvider", new_callable=AsyncMock, side_effect=routed_ping):
        # warm the cache for provider B
        spec_b = await svc.get_spec("0xbbbb", "0x01")
        assert spec_b == VENICE_API

        # provider A's ping is blocked on `block`
        task_a = asyncio.create_task(svc.get_spec("0xaaaa", "0x01"))
        await asyncio.sleep(0.05)  # let task_a reach and block on the ping

        # provider B's lookup must be served from cache without waiting on A
        result_b = await asyncio.wait_for(svc.get_spec("0xbbbb", "0x01"), timeout=0.5)
        assert result_b == VENICE_API
        assert not task_a.done()

        block.set()
        spec_a = await task_a
        assert spec_a == VENICE_API


async def test_get_providers_failure_is_backed_off():
    now = [1000.0]
    svc = _svc(now)
    with patch.object(mod.proxy_router_service, "getProviders", new_callable=AsyncMock, side_effect=RuntimeError("down")) as gp, \
         patch.object(mod.proxy_router_service, "pingProvider", new_callable=AsyncMock) as pp:
        assert await svc.get_spec("0xAAAA", "0x01") is None
        assert await svc.get_spec("0xBBBB", "0x01") is None
        assert gp.await_count == 1, "providers-list fetch must be backed off within the negative TTL"
        pp.assert_not_awaited()

        now[0] += 61
        assert await svc.get_spec("0xAAAA", "0x01") is None
        assert gp.await_count == 2, "past the negative TTL, the providers list must be re-fetched"


async def test_unknown_address_forces_refresh_after_30s_but_not_more_often():
    now = [1000.0]
    svc = _svc(now)
    ping = {"models": [{"modelId": "0x01", "api": VENICE_API}]}
    updated_providers = PROVIDERS + [{"Address": "0xCCCC", "Endpoint": "9.9.9.9:4444"}]
    with patch.object(mod.proxy_router_service, "getProviders", new_callable=AsyncMock,
                       side_effect=[PROVIDERS, updated_providers]) as gp, \
         patch.object(mod.proxy_router_service, "pingProvider", new_callable=AsyncMock, return_value=ping) as pp:
        # Populate a fresh, successfully-refreshed endpoint list (well within TTL=600s).
        assert await svc.get_spec("0xaaaa", "0x01") == VENICE_API
        assert gp.await_count == 1

        # "0xCCCC" is unknown to that still-TTL-fresh list. More than 30s
        # since the last successful refresh forces a re-fetch that picks it up.
        now[0] += 31
        assert await svc.get_spec("0xcccc", "0x01") == VENICE_API
        assert gp.await_count == 2, "an address unknown to a fresh list must be found after a forced refresh"

        # A second unknown address within 30s of that forced refresh must not
        # trigger another getProviders call.
        now[0] += 5
        assert await svc.get_spec("0xdddd", "0x01") is None
        assert gp.await_count == 2, "a second unknown address within 30s must not force another refresh"


async def test_forced_refresh_is_bounded_during_outage():
    now = [1000.0]
    svc = _svc(now)
    ping = {"models": [{"modelId": "0x01", "api": VENICE_API}]}
    providers_a_only = [{"Address": "0xAAAA", "Endpoint": "1.2.3.4:3333"}]
    with patch.object(mod.proxy_router_service, "getProviders", new_callable=AsyncMock,
                       return_value=providers_a_only) as gp, \
         patch.object(mod.proxy_router_service, "pingProvider", new_callable=AsyncMock, return_value=ping):
        # One successful refresh: provider A resolves and is cached.
        assert await svc.get_spec("0xaaaa", "0x01") == VENICE_API
        assert gp.await_count == 1

        # Outage begins: every getProviders call from here on fails.
        gp.side_effect = RuntimeError("down")

        # Past the 30s force-refresh window, lookups for 5 distinct unknown
        # addresses must trigger at most one additional getProviders call,
        # not one per address (the refresh-storm this fix bounds).
        now[0] += 31
        for addr in ("0xbbbb", "0xcccc", "0xdddd", "0xeeee", "0xffff"):
            assert await svc.get_spec(addr, "0x01") is None
            now[0] += 1
        assert gp.await_count == 2, "only the first forced attempt during the outage window may call getProviders"

        # Known endpoints must still resolve despite the ongoing outage.
        assert await svc.get_spec("0xaaaa", "0x01") == VENICE_API

        # Once another 30s have elapsed since that forced attempt, exactly
        # one more forced attempt is allowed on the next unknown lookup.
        now[0] += 30
        assert await svc.get_spec("0x1111", "0x01") is None
        assert gp.await_count == 3
