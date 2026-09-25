import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.api.v1.models import spec_enrichment as mod  # noqa: E402
from src.services import provider_api_spec_service as svc_mod  # noqa: E402

CAPS = {"context": 32768, "maxOutput": 4096, "tools": True, "modalities": {"input": ["text", "image"]}}
CAPS_REORDERED = {"tools": True, "modalities": {"input": ["text", "image"]}, "maxOutput": 4096, "context": 32768}
CAPS_OTHER = {"context": 8192, "tools": False}

SPEC_A = {"stack": "vllm", "modelFamily": "qwen3", "thinking": {"mode": "controllable", "default": "on"}, "capabilities": CAPS}
SPEC_B = {"stack": "ollama", "modelFamily": "qwen3", "thinking": {"mode": "controllable", "default": "on"}, "capabilities": CAPS_REORDERED}
SPEC_C = {"stack": "venice", "modelFamily": "llama", "thinking": {"mode": "always_on"}, "capabilities": CAPS_OTHER}


def _with_cache(specs):
    return patch.object(mod.provider_api_spec_service, "cached_specs_for_model", return_value=specs)


def test_one_spec_attaches_all_fields():
    with _with_cache([SPEC_A]):
        assert mod.spec_enrichment("0x01") == {
            "capabilities": CAPS,
            "model_family": "qwen3",
            "thinking": {"mode": "controllable", "default": "on"},
        }


def test_two_agreeing_specs_attach_once_regardless_of_key_order():
    with _with_cache([SPEC_A, SPEC_B]):
        out = mod.spec_enrichment("0x01")
    assert out["capabilities"] == CAPS
    assert out["model_family"] == "qwen3"
    assert out["thinking"] == {"mode": "controllable", "default": "on"}


def test_two_differing_specs_attach_nothing_and_log_debug():
    with _with_cache([SPEC_A, SPEC_C]), patch.object(mod, "logger") as log:
        assert mod.spec_enrichment("0x01") == {}
    log.debug.assert_called_once()
    assert log.debug.call_args.kwargs["distinct_values"] == 2
    assert log.debug.call_args.kwargs["provider_specs"] == 2


def test_agreeing_capabilities_but_differing_family_or_thinking_attach_capabilities_only():
    other = {**SPEC_A, "modelFamily": "qwen2", "thinking": {"mode": "tunable"}}
    with _with_cache([SPEC_A, other]):
        assert mod.spec_enrichment("0x01") == {"capabilities": CAPS}


def test_no_cached_specs_or_no_capabilities_attaches_nothing():
    with _with_cache([]):
        assert mod.spec_enrichment("0x01") == {}
    with _with_cache([{"stack": "vllm", "modelFamily": "qwen3", "thinking": {"mode": "tunable"}}]):
        assert mod.spec_enrichment("0x01") == {}
    with _with_cache([{"capabilities": "junk"}]):
        assert mod.spec_enrichment("0x01") == {}


def test_thinking_requires_a_mode_and_drops_empty_default():
    spec = {"capabilities": CAPS, "thinking": {"mode": "always_on", "default": ""}}
    with _with_cache([spec]):
        assert mod.spec_enrichment("0x01")["thinking"] == {"mode": "always_on"}
    spec = {"capabilities": CAPS, "thinking": {"default": "on"}}
    with _with_cache([spec]):
        assert "thinking" not in mod.spec_enrichment("0x01")


PROVIDERS = [
    {"Address": "0xAAAA", "Endpoint": "1.2.3.4:3333"},
    {"Address": "0xBBBB", "Endpoint": "5.6.7.8:3333"},
]


async def test_cached_specs_for_model_reads_unexpired_entries_across_providers_without_pinging():
    now = [1000.0]
    svc = svc_mod.ProviderApiSpecService(ttl_seconds=600, negative_ttl_seconds=60, clock=lambda: now[0])
    pings = {
        "0xAAAA": {"models": [{"modelId": "0x01", "api": SPEC_A}]},
        "0xBBBB": {"models": [{"modelId": "0x01", "api": SPEC_B}, {"modelId": "0x02", "api": SPEC_C}]},
    }

    async def ping(address, endpoint):
        return pings[address]

    with patch.object(svc_mod.proxy_router_service, "getProviders", new_callable=AsyncMock, return_value=PROVIDERS), \
         patch.object(svc_mod.proxy_router_service, "pingProvider", side_effect=ping) as pp:
        assert svc.cached_specs_for_model("0x01") == []
        await svc.get_spec("0xAAAA", "0x01")
        await svc.get_spec("0xBBBB", "0x01")
        assert pp.call_count == 2

        assert svc.cached_specs_for_model("0x01") == [SPEC_A, SPEC_B]
        assert svc.cached_specs_for_model("0X02") == [SPEC_C]
        assert svc.cached_specs_for_model("0x03") == []
        assert svc.cached_specs_for_model("") == []
        assert pp.call_count == 2, "the accessor must never ping"

        now[0] += 601
        assert svc.cached_specs_for_model("0x01") == [], "expired entries are not reported"
        assert pp.call_count == 2


async def test_cached_specs_for_model_skips_negative_cache_entries():
    now = [1000.0]
    svc = svc_mod.ProviderApiSpecService(ttl_seconds=600, negative_ttl_seconds=60, clock=lambda: now[0])
    with patch.object(svc_mod.proxy_router_service, "getProviders", new_callable=AsyncMock, return_value=PROVIDERS), \
         patch.object(svc_mod.proxy_router_service, "pingProvider", new_callable=AsyncMock, side_effect=RuntimeError("down")):
        assert await svc.get_spec("0xAAAA", "0x01") is None
    assert svc.cached_specs_for_model("0x01") == []
