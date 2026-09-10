import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.api.v1.chat import request_translation as rt  # noqa: E402

VENICE = {
    "stack": "venice",
    "bindings": {
        "reasoning.disable": {"kind": "body_param", "param": "venice_parameters.disable_thinking", "paramType": "boolean", "value": True},
        "reasoning.effort": {"kind": "body_param", "param": "reasoning_effort", "paramType": "enum", "enumValues": ["none", "low", "medium", "high"]},
    },
}
VLLM_QWEN = {
    "stack": "vllm",
    "bindings": {
        "reasoning.disable": {"kind": "template_kwarg", "param": "chat_template_kwargs.enable_thinking", "paramType": "boolean", "value": False},
        "reasoning.enable": {"kind": "template_kwarg", "param": "chat_template_kwargs.enable_thinking", "paramType": "boolean", "value": True},
        "reasoning.budget": {"kind": "body_param", "param": "thinking_token_budget", "paramType": "number"},
    },
}
OLLAMA = {
    "stack": "ollama",
    "bindings": {
        "reasoning.disable": {"kind": "body_param", "param": "reasoning_effort", "paramType": "string", "value": "none"},
        "reasoning.enable": {"kind": "native_body_param", "param": "think", "paramType": "boolean", "value": True, "hint": "native /api/chat only"},
    },
}


def test_extract_intents():
    assert rt.extract_intents({"reasoning": {"enabled": False}}) == {"reasoning.disable": True}
    assert rt.extract_intents({"reasoning": {"enabled": True}}) == {"reasoning.enable": True}
    assert rt.extract_intents({"reasoning": {"effort": "low", "max_tokens": 2048}}) == {"reasoning.effort": "low", "reasoning.budget": 2048}
    assert rt.extract_intents({"reasoning_effort": "none"}) == {"reasoning.disable": True}
    assert rt.extract_intents({"reasoning_effort": "high"}) == {"reasoning.effort": "high"}
    # object wins over the alias when both present
    assert rt.extract_intents({"reasoning": {"effort": "low"}, "reasoning_effort": "high"}) == {"reasoning.effort": "low"}
    assert rt.extract_intents({"messages": []}) == {}
    assert rt.extract_intents({"reasoning": "garbage"}) == {}


def test_set_path_creates_nested_objects():
    body = {}
    rt.set_path(body, "venice_parameters.disable_thinking", True)
    rt.set_path(body, "chat_template_kwargs.enable_thinking", False)
    rt.set_path(body, "top_k", 5)
    assert body == {"venice_parameters": {"disable_thinking": True}, "chat_template_kwargs": {"enable_thinking": False}, "top_k": 5}
    # existing sibling keys are preserved
    body = {"venice_parameters": {"enable_web_search": "on"}}
    rt.set_path(body, "venice_parameters.disable_thinking", True)
    assert body["venice_parameters"] == {"enable_web_search": "on", "disable_thinking": True}


def test_apply_fixed_value_binding_and_removes_canonical_field():
    body = {"messages": [], "reasoning": {"enabled": False}}
    res = rt.apply_spec(body, VENICE)
    assert body == {"messages": [], "venice_parameters": {"disable_thinking": True}}
    assert res.touched is True
    assert res.applied == {"reasoning.disable": "venice_parameters.disable_thinking"}
    assert res.unsupported == []
    assert res.stack == "venice"


def test_apply_caller_value_with_enum_check():
    body = {"reasoning": {"effort": "low"}}
    res = rt.apply_spec(body, VENICE)
    assert body == {"reasoning_effort": "low"}
    assert res.applied == {"reasoning.effort": "reasoning_effort"}

    body = {"reasoning": {"effort": "xhigh"}}  # not in enumValues
    res = rt.apply_spec(body, VENICE)
    assert body == {"reasoning": {"effort": "xhigh"}}, "unsupported value: body untouched"
    assert res.unsupported == ["reasoning.effort"]
    assert res.touched is False


def test_apply_template_kwarg_and_number_budget():
    body = {"reasoning": {"enabled": False, "max_tokens": 512}}
    res = rt.apply_spec(body, VLLM_QWEN)
    assert body == {"chat_template_kwargs": {"enable_thinking": False}, "thinking_token_budget": 512}
    assert res.applied == {"reasoning.disable": "chat_template_kwargs.enable_thinking", "reasoning.budget": "thinking_token_budget"}


def test_apply_reports_native_and_missing_bindings_as_unsupported():
    body = {"reasoning": {"enabled": True}}
    res = rt.apply_spec(body, OLLAMA)
    assert body == {"reasoning": {"enabled": True}}, "native-only binding is never applied"
    assert res.unsupported == ["reasoning.enable"]

    body = {"reasoning": {"enabled": False, "max_tokens": 100}}
    res = rt.apply_spec(body, OLLAMA)
    assert body == {"reasoning_effort": "none"}
    assert res.applied == {"reasoning.disable": "reasoning_effort"}
    assert res.unsupported == ["reasoning.budget"]


def test_apply_without_spec_or_intents_is_noop():
    body = {"messages": [], "reasoning": {"enabled": False}}
    res = rt.apply_spec(body, None)
    assert body == {"messages": [], "reasoning": {"enabled": False}}
    assert res.touched is False and res.applied == {} and res.unsupported == []

    body = {"messages": []}
    res = rt.apply_spec(body, VENICE)
    assert body == {"messages": []}
    assert res.touched is False


def test_alias_removed_together_with_object():
    body = {"reasoning": {"enabled": False}, "reasoning_effort": "high"}
    rt.apply_spec(body, VENICE)
    assert "reasoning" not in body and "reasoning_effort" not in body


def test_headers():
    res = rt.TranslationResult(applied={"reasoning.disable": "venice_parameters.disable_thinking"}, unsupported=["reasoning.budget"], stack="venice", touched=True)
    assert res.headers() == {
        "X-Morpheus-Provider-Stack": "venice",
        "X-Morpheus-Translated": "reasoning.disable=venice_parameters.disable_thinking",
        "X-Morpheus-Unsupported": "reasoning.budget",
    }
    assert rt.TranslationResult().headers() == {}


def test_headers_sanitizes_untrusted_provider_values():
    # spec["stack"] and binding["param"] come from an untrusted provider
    # report; CRLF/non-latin-1 characters must never reach the header dict.
    res = rt.TranslationResult(
        stack="vllm\r\nX-Injected: 1é",
        applied={"reasoning.disable": "chat_template_kwargs.enable_thinking"},
        unsupported=["reasoning.budget"],
        touched=True,
    )
    headers = res.headers()
    assert headers["X-Morpheus-Provider-Stack"] == "vllmX-Injected1"
    assert headers["X-Morpheus-Translated"] == "reasoning.disable=chat_template_kwargs.enable_thinking"
    assert headers["X-Morpheus-Unsupported"] == "reasoning.budget"


def test_headers_omits_stack_header_when_all_chars_unsafe():
    res = rt.TranslationResult(stack="日本語")
    assert "X-Morpheus-Provider-Stack" not in res.headers()


def test_headers_caps_element_length():
    res = rt.TranslationResult(stack="a" * 200)
    assert res.headers()["X-Morpheus-Provider-Stack"] == "a" * 128


def test_headers_omits_applied_pair_when_intent_or_param_all_unsafe():
    res = rt.TranslationResult(applied={"reasoning.disable": "日本語"}, touched=True)
    assert "X-Morpheus-Translated" not in res.headers()


async def test_translate_for_session_disabled_is_noop():
    body = {"reasoning": {"enabled": False}}
    with patch.object(rt.settings, "REQUEST_TRANSLATION_ENABLED", False):
        res = await rt.translate_for_session(body, "0xsess", "0x01")
    assert body == {"reasoning": {"enabled": False}} and res.touched is False


async def test_translate_for_session_resolves_provider_and_applies():
    body = {"reasoning": {"enabled": False}}
    row = MagicMock()
    row.provider_address = "0xAAAA"

    class FakeGetDb:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            return False

    with patch.object(rt.settings, "REQUEST_TRANSLATION_ENABLED", True), \
         patch.object(rt, "get_db", lambda: FakeGetDb()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=row), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=VENICE) as gs:
        res = await rt.translate_for_session(body, "0xsess", "0x01")
    gs.assert_awaited_once_with("0xAAAA", "0x01")
    assert body == {"venice_parameters": {"disable_thinking": True}}
    assert res.applied == {"reasoning.disable": "venice_parameters.disable_thinking"}
    # success path must mutate the caller's dict in place (handlers rely on this)
    assert "reasoning" not in body and "venice_parameters" in body


async def test_translate_for_session_never_raises_on_malformed_binding():
    # provider-reported spec with a non-string param: set_path would raise AttributeError.
    # apply_spec now catches this per-binding, so it's reported unsupported rather than
    # discarding the whole translation via the outer guard.
    bad_spec = {"stack": "vllm", "bindings": {"reasoning.disable": {
        "kind": "body_param", "param": 123, "paramType": "boolean", "value": True}}}
    body = {"messages": [], "reasoning": {"enabled": False}}
    row = MagicMock()
    row.provider_address = "0xprov"

    class FakeGetDb:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            return False

    with patch.object(rt.settings, "REQUEST_TRANSLATION_ENABLED", True), \
         patch.object(rt, "get_db", lambda: FakeGetDb()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=row), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=bad_spec):
        result = await rt.translate_for_session(body, "0xsess", "0x01")
    assert result.applied == {} and result.unsupported == ["reasoning.disable"] and not result.touched
    assert body == {"messages": [], "reasoning": {"enabled": False}}, "canonical field is only popped when something applied"


def test_apply_spec_tolerates_malformed_binding_alongside_valid_one():
    # One good binding applies cleanly; a malformed sibling binding (non-string
    # param) is reported unsupported instead of discarding the whole result.
    mixed_spec = {
        "stack": "vllm",
        "bindings": {
            "reasoning.enable": {"kind": "body_param", "param": "reasoning_mode", "paramType": "string", "value": "on"},
            "reasoning.budget": {"kind": "body_param", "param": 123, "paramType": "number"},
        },
    }
    body = {"messages": [], "reasoning": {"enabled": True, "max_tokens": 100}}
    res = rt.apply_spec(body, mixed_spec)
    assert res.applied == {"reasoning.enable": "reasoning_mode"}
    assert res.unsupported == ["reasoning.budget"]
    assert res.touched is True
    assert body == {"messages": [], "reasoning_mode": "on"}, "canonical reasoning key removed: at least one intent applied"


def test_apply_spec_rejects_protected_root_param():
    spec = {"stack": "evil", "bindings": {"reasoning.disable": {
        "kind": "body_param", "param": "messages", "paramType": "string", "value": "override"}}}
    body = {"messages": [{"role": "user", "content": "hi"}], "reasoning": {"enabled": False}}
    res = rt.apply_spec(body, spec)
    assert res.unsupported == ["reasoning.disable"]
    assert res.applied == {}
    assert res.touched is False
    assert body["messages"] == [{"role": "user", "content": "hi"}], "protected root must never be overwritten"


def test_apply_spec_rejects_template_kwarg_without_chat_template_kwargs_prefix():
    spec = {"stack": "evil", "bindings": {"reasoning.disable": {
        "kind": "template_kwarg", "param": "enable_thinking", "paramType": "boolean", "value": False}}}
    body = {"reasoning": {"enabled": False}}
    res = rt.apply_spec(body, spec)
    assert res.unsupported == ["reasoning.disable"]
    assert res.touched is False
    assert body == {"reasoning": {"enabled": False}}


def test_apply_spec_still_applies_prefixed_vllm_template_kwarg():
    # Regression: a legitimately-prefixed template_kwarg binding (the existing
    # vLLM fixture) must keep applying after the prefix check is added.
    body = {"reasoning": {"enabled": False}}
    res = rt.apply_spec(body, VLLM_QWEN)
    assert res.applied == {"reasoning.disable": "chat_template_kwargs.enable_thinking"}
    assert body == {"chat_template_kwargs": {"enable_thinking": False}}


async def test_translate_for_session_without_provider_is_noop():
    body = {"reasoning": {"enabled": False}}
    row = MagicMock()
    row.provider_address = None

    class FakeGetDb:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            return False

    with patch.object(rt.settings, "REQUEST_TRANSLATION_ENABLED", True), \
         patch.object(rt, "get_db", lambda: FakeGetDb()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=row), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock) as gs:
        res = await rt.translate_for_session(body, "0xsess", "0x01")
    gs.assert_not_awaited()
    assert body == {"reasoning": {"enabled": False}} and res.touched is False


async def test_translate_for_session_without_intents_skips_lookups():
    body = {"messages": []}
    with patch.object(rt.settings, "REQUEST_TRANSLATION_ENABLED", True), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock) as mock_get_session, \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock) as mock_get_spec:
        res = await rt.translate_for_session(body, "0xsess", "0x01")
    mock_get_session.assert_not_awaited()
    mock_get_spec.assert_not_awaited()
    assert body == {"messages": []} and res.touched is False


def test_alias_ignored_when_object_present():
    # reasoning object contributes intents, so alias should be ignored
    result = rt.extract_intents({"reasoning": {"enabled": True}, "reasoning_effort": "none"})
    assert result == {"reasoning.enable": True}, f"Expected only reasoning.enable, got {result}"

    # reasoning object contributes intents (budget), so alias should be ignored
    result = rt.extract_intents({"reasoning": {"max_tokens": 100}, "reasoning_effort": "high"})
    assert result == {"reasoning.budget": 100}, f"Expected only reasoning.budget, got {result}"
