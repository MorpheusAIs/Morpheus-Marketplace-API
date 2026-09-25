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
    logged = {c.kwargs["field"]: c.kwargs for c in log.debug.call_args_list}
    assert set(logged) == {"capabilities", "model_family", "thinking"}
    assert logged["capabilities"]["distinct_values"] == 2
    assert logged["capabilities"]["provider_specs"] == 2


def test_agreeing_capabilities_but_differing_family_or_thinking_attach_capabilities_only():
    other = {**SPEC_A, "modelFamily": "qwen2", "thinking": {"mode": "tunable"}}
    with _with_cache([SPEC_A, other]):
        assert mod.spec_enrichment("0x01") == {"capabilities": CAPS}


def test_no_cached_specs_or_nothing_usable_attaches_nothing():
    with _with_cache([]):
        assert mod.spec_enrichment("0x01") == {}
    with _with_cache([{"stack": "vllm"}]):
        assert mod.spec_enrichment("0x01") == {}
    with _with_cache([{"capabilities": "junk"}]):
        assert mod.spec_enrichment("0x01") == {}
    with _with_cache([{"capabilities": {}}, {"capabilities": {"unknown": 1}}]):
        assert mod.spec_enrichment("0x01") == {}, "nothing declared survives projection"


def test_family_and_thinking_attach_without_capabilities():
    """A router that reports modelFamily and thinking but no capabilities block still enriches the row."""
    with _with_cache([{"stack": "vllm", "modelFamily": "qwen3", "thinking": {"mode": "tunable"}}]):
        assert mod.spec_enrichment("0x01") == {"model_family": "qwen3", "thinking": {"mode": "tunable"}}
    disagree = {**SPEC_A, "capabilities": CAPS_OTHER}
    with _with_cache([SPEC_A, disagree]):
        assert mod.spec_enrichment("0x01") == {"model_family": "qwen3", "thinking": {"mode": "controllable", "default": "on"}}


def test_capabilities_are_projected_onto_declared_fields_without_coercion():
    junk = {**CAPS, "vendor_blob": "x" * 10000, "nested": {"a": [1, 2]}}
    with _with_cache([{"capabilities": junk}]):
        assert mod.spec_enrichment("0x01") == {"capabilities": CAPS}
    with _with_cache([{"capabilities": junk}, {"capabilities": {**CAPS, "other_blob": 1}}]):
        assert mod.spec_enrichment("0x01")["capabilities"] == CAPS, "extras never make specs disagree"
    full = {"context": 1, "contextTrained": 2, "maxOutput": 3, "modalities": {"input": ["text"], "output": ["text"]},
            "tools": True, "toolsParallel": False, "structured": ["json_schema"], "reasoning": True,
            "webSearch": False, "embeddings": False, "promptCache": True, "quantization": "fp8"}
    with _with_cache([{"capabilities": full}]):
        assert mod.spec_enrichment("0x01") == {"capabilities": full}, "every declared field passes through"
    for bad in ({"context": "32768"}, {"tools": 1}, {"quantization": "q" * 65}, {"modalities": {"input": ["a"] * 17}},
                {"structured": ["s" * 65]}, {"context": 1.0}):
        with _with_cache([{"capabilities": {**CAPS, **bad}}]), patch.object(mod, "logger") as log:
            assert mod.spec_enrichment("0x01") == {}, bad
        assert log.debug.call_args.kwargs["event_type"] == "models_capabilities_invalid"


def test_family_and_thinking_strings_are_bounded():
    with _with_cache([{"modelFamily": "f" * 65, "thinking": {"mode": "m" * 65}}]):
        assert mod.spec_enrichment("0x01") == {}
    with _with_cache([{"modelFamily": "f" * 64, "thinking": {"mode": "always_on", "default": "d" * 65}}]):
        assert mod.spec_enrichment("0x01") == {"model_family": "f" * 64, "thinking": {"mode": "always_on"}}


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
