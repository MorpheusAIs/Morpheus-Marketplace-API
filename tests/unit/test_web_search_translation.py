import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.api.v1.chat import request_translation as rt  # noqa: E402
from src.api.v1.chat import web_search_translation as ws  # noqa: E402
from src.core.direct_model_service import DirectModelService  # noqa: E402

ROOTS = rt.PROTECTED_ROOTS

# Binding rows as proxy-router/internal/apispec/presets.go declares them
VENICE = {
    "stack": "venice",
    "capabilities": {"webSearch": True},
    "bindings": {
        "reasoning.disable": {"kind": "body_param", "param": "venice_parameters.disable_thinking", "paramType": "boolean", "value": True},
        "web_search.enable": {"kind": "body_param", "param": "venice_parameters.enable_web_search", "paramType": "string", "value": "on"},
        "web_search.citations": {"kind": "body_param", "param": "venice_parameters.enable_web_citations", "paramType": "boolean", "value": True},
    },
}
OPENROUTER = {
    "stack": "openrouter",
    "bindings": {
        "web_search.enable": {"kind": "body_param", "param": "plugins", "paramType": "array", "value": [{"id": "web"}]},
    },
}
LITELLM = {
    "stack": "litellm",
    "bindings": {
        "web_search.enable": {"kind": "body_param", "param": "web_search_options", "paramType": "object", "value": {}},
    },
}
ANTHROPIC = {
    "stack": "anthropic",
    "bindings": {
        "web_search.enable": {"kind": "tool_entry", "param": "tools", "paramType": "object",
                              "value": {"type": "web_search_20250305", "name": "web_search"}},
    },
}
TEMPLATE = {
    "stack": "vllm",
    "bindings": {
        "web_search.enable": {"kind": "template_kwarg", "param": "chat_template_kwargs.enable_search", "paramType": "boolean", "value": True},
    },
}
WEB_TOOL = {"type": "web_search_20250305", "name": "web_search"}


def test_extract_web_search_intents():
    assert ws.extract_web_search_intents({"web_search": {"enabled": True}}) == {"web_search.enable": True}
    assert ws.extract_web_search_intents({"web_search": {"enabled": True, "citations": True}}) == {
        "web_search.enable": True, "web_search.citations": True}
    assert ws.extract_web_search_intents({"web_search": {"enabled": True, "citations": False}}) == {"web_search.enable": True}
    assert ws.extract_web_search_intents({"web_search": {"enabled": False, "citations": True}}) == {}, "citations only when enabled"
    assert ws.extract_web_search_intents({"web_search": {"citations": True}}) == {}, "enabled must be explicit"
    assert ws.extract_web_search_intents({"web_search_options": {}}) == {"web_search.enable": True}, "alias"
    assert ws.extract_web_search_intents({"web_search": {"enabled": False}, "web_search_options": {}}) == {}, "canonical wins over alias"
    assert ws.extract_web_search_intents({"web_search": "garbage", "web_search_options": {"search_context_size": "low"}}) == {"web_search.enable": True}
    assert ws.extract_web_search_intents({"web_search_options": "garbage"}) == {}
    assert ws.extract_web_search_intents({"messages": []}) == {}


def test_extract_intents_in_request_translation_covers_web_search_and_reasoning():
    body = {"reasoning": {"enabled": False}, "web_search": {"enabled": True, "citations": True}}
    assert rt.extract_intents(body) == {
        "reasoning.disable": True, "web_search.enable": True, "web_search.citations": True}
    assert rt.needs_translation({"web_search": {"enabled": True}}) is True
    assert rt.needs_translation({"web_search_options": {}}) is True
    assert rt.needs_translation({"web_search": {"enabled": False}}) is True, "the disabled field is removed"
    assert rt.needs_translation({"messages": []}) is False


def test_body_param_scalar_and_citations():
    body = {"messages": [], "web_search": {"enabled": True, "citations": True}}
    res = ws.apply_web_search(body, VENICE, ROOTS)
    assert body == {"messages": [], "venice_parameters": {"enable_web_search": "on", "enable_web_citations": True}}
    assert res.applied == {"web_search.enable": "venice_parameters.enable_web_search",
                           "web_search.citations": "venice_parameters.enable_web_citations"}
    assert res.unsupported == [] and res.touched is True and res.mismatch is False


def test_body_param_keeps_sibling_native_fields():
    body = {"venice_parameters": {"disable_thinking": True}, "web_search": {"enabled": True}}
    ws.apply_web_search(body, VENICE, ROOTS)
    assert body == {"venice_parameters": {"disable_thinking": True, "enable_web_search": "on"}}


def test_array_binding_extends_existing_array_without_duplicates():
    body = {"plugins": [{"id": "file-parser"}], "web_search": {"enabled": True}}
    res = ws.apply_web_search(body, OPENROUTER, ROOTS)
    assert body == {"plugins": [{"id": "file-parser"}, {"id": "web"}]}
    assert res.applied == {"web_search.enable": "plugins"}

    body = {"plugins": [{"id": "web"}], "web_search": {"enabled": True}}
    ws.apply_web_search(body, OPENROUTER, ROOTS)
    assert body == {"plugins": [{"id": "web"}]}, "already present: not appended twice"


def test_array_binding_creates_or_overwrites_non_array_target():
    body = {"web_search": {"enabled": True}}
    ws.apply_web_search(body, OPENROUTER, ROOTS)
    assert body == {"plugins": [{"id": "web"}]}

    body = {"plugins": "web", "web_search": {"enabled": True}}
    ws.apply_web_search(body, OPENROUTER, ROOTS)
    assert body == {"plugins": [{"id": "web"}]}


def test_array_binding_value_is_not_shared_with_the_spec():
    body = {"web_search": {"enabled": True}}
    ws.apply_web_search(body, OPENROUTER, ROOTS)
    body["plugins"].append({"id": "later"})
    assert OPENROUTER["bindings"]["web_search.enable"]["value"] == [{"id": "web"}]


def test_alias_leaves_web_search_options_untouched():
    body = {"messages": [], "web_search_options": {"search_context_size": "high"}}
    res = ws.apply_web_search(body, LITELLM, ROOTS)
    assert body == {"messages": [], "web_search_options": {"search_context_size": "high"}}
    assert res.applied == {"web_search.enable": "web_search_options"}
    assert res.touched is True

    body = {"web_search_options": {"search_context_size": "high"}}
    ws.apply_web_search(body, VENICE, ROOTS)
    assert body == {"web_search_options": {"search_context_size": "high"}, "venice_parameters": {"enable_web_search": "on"}}


def test_object_binding_creates_object_when_absent():
    body = {"web_search": {"enabled": True}}
    ws.apply_web_search(body, LITELLM, ROOTS)
    assert body == {"web_search_options": {}}


def test_template_kwarg_binding():
    body = {"web_search": {"enabled": True}}
    res = ws.apply_web_search(body, TEMPLATE, ROOTS)
    assert body == {"chat_template_kwargs": {"enable_search": True}}
    assert res.applied == {"web_search.enable": "chat_template_kwargs.enable_search"}

    bad = {"stack": "vllm", "bindings": {"web_search.enable": {
        "kind": "template_kwarg", "param": "enable_search", "paramType": "boolean", "value": True}}}
    body = {"web_search": {"enabled": True}}
    res = ws.apply_web_search(body, bad, ROOTS)
    assert res.unsupported == ["web_search.enable"] and body == {"web_search": {"enabled": True}}


def test_tool_entry_creates_tools_and_is_idempotent():
    body = {"messages": [], "web_search": {"enabled": True}}
    res = ws.apply_web_search(body, ANTHROPIC, ROOTS)
    assert body == {"messages": [], "tools": [WEB_TOOL]}
    assert res.applied == {"web_search.enable": "tools"}

    body = {"tools": [{"type": "function", "function": {"name": "f"}}], "web_search": {"enabled": True}}
    ws.apply_web_search(body, ANTHROPIC, ROOTS)
    assert body["tools"] == [{"type": "function", "function": {"name": "f"}}, WEB_TOOL]

    body = {"tools": [dict(WEB_TOOL)], "web_search": {"enabled": True}}
    res = ws.apply_web_search(body, ANTHROPIC, ROOTS)
    assert body == {"tools": [WEB_TOOL]}, "equal entry already present: not appended"
    assert res.applied == {"web_search.enable": "tools"}


def test_tool_entry_appends_a_copy():
    body = {"web_search": {"enabled": True}}
    ws.apply_web_search(body, ANTHROPIC, ROOTS)
    body["tools"][0]["name"] = "changed"
    assert ANTHROPIC["bindings"]["web_search.enable"]["value"]["name"] == "web_search"


def test_tool_entry_rejects_non_list_tools_wrong_param_or_non_object_value():
    body = {"tools": "auto", "web_search": {"enabled": True}}
    res = ws.apply_web_search(body, ANTHROPIC, ROOTS)
    assert res.unsupported == ["web_search.enable"] and body == {"tools": "auto", "web_search": {"enabled": True}}

    for binding in (
        {"kind": "tool_entry", "param": "plugins", "value": WEB_TOOL},
        {"kind": "tool_entry", "param": "tools", "value": "web_search"},
    ):
        body = {"web_search": {"enabled": True}}
        res = ws.apply_web_search(body, {"stack": "x", "bindings": {"web_search.enable": binding}}, ROOTS)
        assert res.unsupported == ["web_search.enable"] and "tools" not in body


def test_body_param_never_writes_protected_roots():
    spec = {"stack": "evil", "bindings": {"web_search.enable": {"kind": "body_param", "param": "tools", "value": [WEB_TOOL]}}}
    body = {"tools": [], "web_search": {"enabled": True}}
    res = ws.apply_web_search(body, spec, ROOTS)
    assert res.unsupported == ["web_search.enable"] and body["tools"] == []


def test_disabled_removes_canonical_field_and_applies_nothing():
    body = {"messages": [], "web_search": {"enabled": False}}
    res = ws.apply_web_search(body, VENICE, ROOTS)
    assert body == {"messages": []}
    assert res.applied == {} and res.unsupported == [] and res.touched is True

    body = {"web_search": {"enabled": False}}
    res = ws.apply_web_search(body, None, ROOTS)
    assert body == {} and res.touched is True


def test_unsupported_kinds_and_missing_binding():
    for kind in ("system_prompt", "native_body_param", "unknown"):
        spec = {"stack": "x", "bindings": {"web_search.enable": {"kind": kind, "param": "search", "value": True, "hint": "..."}}}
        body = {"web_search": {"enabled": True}}
        res = ws.apply_web_search(body, spec, ROOTS)
        assert res.unsupported == ["web_search.enable"], kind
        assert body == {"web_search": {"enabled": True}}, "body untouched"
        assert res.touched is False

    body = {"web_search": {"enabled": True, "citations": True}}
    res = ws.apply_web_search(body, OPENROUTER, ROOTS)
    assert body == {"plugins": [{"id": "web"}]}
    assert res.applied == {"web_search.enable": "plugins"} and res.unsupported == ["web_search.citations"]


def test_enum_binding_rejects_value_outside_enum():
    spec = {"stack": "x", "bindings": {"web_search.enable": {
        "kind": "body_param", "param": "search_mode", "paramType": "enum", "enumValues": ["off", "auto"], "value": "on"}}}
    body = {"web_search": {"enabled": True}}
    res = ws.apply_web_search(body, spec, ROOTS)
    assert res.unsupported == ["web_search.enable"] and body == {"web_search": {"enabled": True}}


def test_without_spec_or_intents_is_noop():
    body = {"web_search": {"enabled": True}}
    res = ws.apply_web_search(body, None, ROOTS)
    assert body == {"web_search": {"enabled": True}} and res.touched is False and res.unsupported == []

    body = {"messages": []}
    res = ws.apply_web_search(body, VENICE, ROOTS)
    assert body == {"messages": []} and res.touched is False


def test_mismatch_when_tag_present_and_spec_denies():
    denies = {"stack": "vllm", "capabilities": {"webSearch": False}, "bindings": VENICE["bindings"]}
    body = {"messages": [], "web_search": {"enabled": True, "citations": True}}
    res = ws.apply_web_search(body, denies, ROOTS, model_tags=["web-search-enabled", "textgeneration"])
    assert res.mismatch is True
    assert body == {"messages": [], "web_search": {"enabled": True, "citations": True}}, "forwarded unchanged"
    assert res.applied == {} and res.unsupported == ["web_search.enable", "web_search.citations"]
    assert res.touched is False


def test_no_mismatch_without_tag_or_with_unknown_capability():
    denies = {"stack": "venice", "capabilities": {"webSearch": False}, "bindings": VENICE["bindings"]}
    body = {"web_search": {"enabled": True}}
    res = ws.apply_web_search(body, denies, ROOTS, model_tags=["textgeneration"])
    assert res.mismatch is False and res.applied == {"web_search.enable": "venice_parameters.enable_web_search"}

    unknown = {"stack": "venice", "bindings": VENICE["bindings"]}
    body = {"web_search": {"enabled": True}}
    res = ws.apply_web_search(body, unknown, ROOTS, model_tags=["web-search-enabled"])
    assert res.mismatch is False and res.applied == {"web_search.enable": "venice_parameters.enable_web_search"}

    body = {"web_search": {"enabled": True}}
    res = ws.apply_web_search(body, denies, ROOTS, model_tags=None)
    assert res.mismatch is False

    body = {"web_search": {"enabled": False}}
    res = ws.apply_web_search(body, denies, ROOTS, model_tags=["web-search-enabled"])
    assert res.mismatch is False and body == {}, "disabled: nothing to reconcile"


def test_mismatch_header():
    res = rt.TranslationResult(stack="vllm", unsupported=["web_search.enable"], mismatch=["web_search"])
    assert res.headers() == {
        "X-Morpheus-Provider-Stack": "vllm",
        "X-Morpheus-Unsupported": "web_search.enable",
        "X-Morpheus-Capability-Mismatch": "web_search",
    }
    assert "X-Morpheus-Capability-Mismatch" not in rt.TranslationResult(mismatch=["日本語"]).headers()


def test_translated_header_lists_web_search_like_reasoning():
    res = rt.TranslationResult(
        applied={"reasoning.disable": "venice_parameters.disable_thinking", "web_search.enable": "tools"}, touched=True)
    assert res.headers()["X-Morpheus-Translated"] == "reasoning.disable=venice_parameters.disable_thinking;web_search.enable=tools"


def test_apply_spec_leaves_web_search_intents_to_the_web_search_pass():
    body = {"reasoning": {"enabled": False}, "web_search": {"enabled": True}}
    res = rt.apply_spec(body, VENICE)
    assert res.applied == {"reasoning.disable": "venice_parameters.disable_thinking"}
    assert res.unsupported == []
    assert body == {"venice_parameters": {"disable_thinking": True}, "web_search": {"enabled": True}}


def test_direct_model_service_get_model_tags():
    svc = DirectModelService(cache_duration_seconds=300)
    svc._update_cache([
        {"Id": "0x01", "Name": "search-model", "Tags": ["web-search-enabled", 7, "textgeneration"]},
        {"Id": "0x02", "Name": "plain-model"},
    ], content_hash="h", etag=None)
    with patch.object(svc, "_ensure_fresh_cache", new_callable=AsyncMock):
        import asyncio
        assert asyncio.run(svc.get_model_tags("0x01")) == ["web-search-enabled", "textgeneration"]
        assert asyncio.run(svc.get_model_tags("0x02")) == []
        assert asyncio.run(svc.get_model_tags("0x99")) == []


@pytest.fixture
def translation_enabled():
    token = rt.set_request_translation(True)
    try:
        yield
    finally:
        rt._TRANSLATION_ACTIVE.reset(token)


class _FakeGetDb:
    async def __aenter__(self):
        return AsyncMock()

    async def __aexit__(self, *args):
        return False


def _session_row(provider="0xprov"):
    row = MagicMock()
    row.provider_address = provider
    return row


async def test_translate_for_session_applies_web_search_with_reasoning(translation_enabled):
    body = {"messages": [], "reasoning": {"enabled": False}, "web_search": {"enabled": True, "citations": True}}
    with patch.object(rt, "get_db", lambda: _FakeGetDb()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=_session_row()), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=VENICE), \
         patch.object(rt.direct_model_service, "get_model_tags", new_callable=AsyncMock, return_value=["web-search-enabled"]) as tags:
        res = await rt.translate_for_session(body, "0xsess", "0x01")
    tags.assert_awaited_once_with("0x01")
    assert body == {"messages": [], "venice_parameters": {
        "disable_thinking": True, "enable_web_search": "on", "enable_web_citations": True}}
    assert res.applied == {
        "reasoning.disable": "venice_parameters.disable_thinking",
        "web_search.enable": "venice_parameters.enable_web_search",
        "web_search.citations": "venice_parameters.enable_web_citations",
    }
    assert res.mismatch == [] and res.touched is True
    assert res.headers()["X-Morpheus-Translated"].startswith("reasoning.disable=")


async def test_translate_for_session_web_search_only_sets_stack(translation_enabled):
    body = {"web_search": {"enabled": True}}
    with patch.object(rt, "get_db", lambda: _FakeGetDb()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=_session_row()), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=ANTHROPIC), \
         patch.object(rt.direct_model_service, "get_model_tags", new_callable=AsyncMock, return_value=[]):
        res = await rt.translate_for_session(body, "0xsess", "0x01")
    assert body == {"tools": [WEB_TOOL]}
    assert res.stack == "anthropic" and res.applied == {"web_search.enable": "tools"}


async def test_translate_for_session_mismatch_keeps_body_but_applies_reasoning(translation_enabled):
    denies = {"stack": "vllm", "capabilities": {"webSearch": False}, "bindings": VENICE["bindings"]}
    body = {"messages": [], "reasoning": {"enabled": False}, "web_search": {"enabled": True}}
    with patch.object(rt, "get_db", lambda: _FakeGetDb()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=_session_row()), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=denies), \
         patch.object(rt.direct_model_service, "get_model_tags", new_callable=AsyncMock, return_value=["web-search-enabled"]), \
         patch.object(rt.logger, "warning") as warn:
        res = await rt.translate_for_session(body, "0xsess", "0x01")
    assert body == {"messages": [], "venice_parameters": {"disable_thinking": True}, "web_search": {"enabled": True}}
    assert res.mismatch == ["web_search"]
    assert res.applied == {"reasoning.disable": "venice_parameters.disable_thinking"}
    assert res.unsupported == ["web_search.enable"]
    headers = res.headers()
    assert headers["X-Morpheus-Capability-Mismatch"] == "web_search"
    assert headers["X-Morpheus-Unsupported"] == "web_search.enable"
    assert any(call.kwargs.get("event_type") == "request_translation_capability_mismatch" for call in warn.call_args_list)


async def test_translate_for_session_skips_tag_lookup_without_web_search_intent(translation_enabled):
    body = {"reasoning": {"enabled": False}}
    with patch.object(rt, "get_db", lambda: _FakeGetDb()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=_session_row()), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=VENICE), \
         patch.object(rt.direct_model_service, "get_model_tags", new_callable=AsyncMock) as tags:
        await rt.translate_for_session(body, "0xsess", "0x01")
    tags.assert_not_awaited()
    assert body == {"venice_parameters": {"disable_thinking": True}}


async def test_translate_for_session_tag_lookup_failure_translates_without_mismatch(translation_enabled):
    denies = {"stack": "venice", "capabilities": {"webSearch": False}, "bindings": VENICE["bindings"]}
    body = {"web_search": {"enabled": True}}
    with patch.object(rt, "get_db", lambda: _FakeGetDb()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=_session_row()), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=denies), \
         patch.object(rt.direct_model_service, "get_model_tags", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        res = await rt.translate_for_session(body, "0xsess", "0x01")
    assert res.mismatch == []
    assert body == {"venice_parameters": {"enable_web_search": "on"}}


async def test_translate_for_session_disabled_removes_field_even_without_spec(translation_enabled):
    body = {"messages": [], "web_search": {"enabled": False}}
    with patch.object(rt, "get_db", lambda: _FakeGetDb()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=_session_row()), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=None), \
         patch.object(rt.direct_model_service, "get_model_tags", new_callable=AsyncMock) as tags:
        res = await rt.translate_for_session(body, "0xsess", "0x01")
    tags.assert_not_awaited()
    assert body == {"messages": []} and res.touched is True and res.headers() == {}
