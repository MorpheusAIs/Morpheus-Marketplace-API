import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src import main as main_module  # noqa: E402
from src.api.v1.chat import index as chat_index  # noqa: E402
from src.api.v1.chat import request_translation as rt  # noqa: E402
from src.api.v1.chat import unlisted_params as up  # noqa: E402
from src.api.v1.chat import chat_non_streaming, chat_streaming  # noqa: E402
from src.api.v1.chat.chat_models import ChatCompletionRequest  # noqa: E402

# Parameter rows as proxy-router/internal/apispec/presets.go declares them
VLLM_PARAMS = ["model", "messages", "temperature", "top_p", "n", "stream", "stream_options", "stop", "max_tokens",
               "max_completion_tokens", "presence_penalty", "frequency_penalty", "logit_bias", "logprobs",
               "top_logprobs", "seed", "response_format", "tools", "tool_choice", "parallel_tool_calls",
               "reasoning_effort"]
VLLM = {
    "stack": "vllm",
    "parameters": VLLM_PARAMS,
    "bindings": {
        "reasoning.disable": {"kind": "template_kwarg", "param": "chat_template_kwargs.enable_thinking",
                              "paramType": "boolean", "value": False},
    },
}
VENICE = {
    "stack": "venice",
    "parameters": ["model", "messages", "temperature", "max_tokens", "tools"],
    "bindings": {
        "reasoning.disable": {"kind": "body_param", "param": "venice_parameters.disable_thinking",
                              "paramType": "boolean", "value": True},
        "web_search.enable": {"kind": "body_param", "param": "venice_parameters.enable_web_search",
                              "paramType": "string", "value": "on"},
    },
}
ANTHROPIC = {
    "stack": "anthropic",
    "parameters": ["model", "messages", "system", "max_tokens", "stream", "temperature", "top_p", "top_k",
                   "stop_sequences", "tools", "tool_choice", "metadata"],
    "bindings": {
        "web_search.enable": {"kind": "tool_entry", "param": "tools", "paramType": "object",
                              "value": {"type": "web_search_20250305", "name": "web_search"}},
    },
}
STANDARD_BODY = {"model": "m", "messages": [], "temperature": 0.2, "max_tokens": 10, "stream": True,
                 "tools": [], "reasoning_effort": "low"}


def test_empty_or_absent_parameters_yields_nothing():
    body = {**STANDARD_BODY, "foo": 1}
    assert up.unlisted_params(body, {"stack": "vllm"}) == []
    assert up.unlisted_params(body, {"stack": "vllm", "parameters": []}) == []
    assert up.unlisted_params(body, {"stack": "vllm", "parameters": "model"}) == [], "malformed list treated as absent"
    assert up.unlisted_params(body, None) == []
    assert up.unlisted_headers(body, {"stack": "vllm"}) == {}


def test_standard_and_listed_keys_are_never_reported():
    body = {**STANDARD_BODY, "user": "u1", "logit_bias": {}}
    assert up.unlisted_params(body, VLLM) == [], "user is standard OpenAI even though vllm does not list it"
    assert up.unlisted_headers(body, VLLM) == {}


def test_unlisted_keys_are_reported_sorted():
    body = {**STANDARD_BODY, "top_k": 40, "min_p": 0.1, "repetition_penalty": 1.1}
    assert up.unlisted_params(body, VLLM) == ["min_p", "repetition_penalty", "top_k"]
    assert up.unlisted_headers(body, VLLM) == {"X-Morpheus-Unlisted-Params": "min_p;repetition_penalty;top_k"}


def test_gateway_canonical_fields_are_excluded():
    body = {**STANDARD_BODY, "reasoning": {"enabled": False}, "web_search": {"enabled": True}}
    assert up.unlisted_params(body, VLLM) == []


def test_routing_fields_the_handlers_strip_are_excluded():
    """session_id is a documented ChatCompletionRequest field and request_id rides along; neither reaches the provider."""
    body = ChatCompletionRequest.model_validate({"model": "m", "messages": [{"role": "user", "content": "hi"}],
                                                 "session_id": "0xabc", "request_id": "r1"}).model_dump(exclude_none=True)
    assert up.unlisted_params(body, VLLM) == []
    assert up.unlisted_headers(body, VLLM) == {}


def test_handlers_strip_exactly_the_routing_fields_the_check_excludes():
    raw = b'{"messages": [], "stream": true, "session_id": "0xabc", "request_id": "r1", "min_p": 0.1}'
    for _, params in (chat_non_streaming._parse_request(raw), chat_streaming._parse_request_body(raw, MagicMock())):
        assert set(params) == {"min_p"}
        assert up.ROUTING_FIELDS.isdisjoint(params)
    assert up.ROUTING_FIELDS <= up.GATEWAY_FIELDS


def test_binding_targeted_roots_are_excluded():
    body = {**STANDARD_BODY, "venice_parameters": {"include_venice_system_prompt": False},
            "chat_template_kwargs": {"enable_thinking": False}, "min_p": 0.1}
    assert up.unlisted_params(body, VENICE) == ["chat_template_kwargs", "min_p"]
    assert up.unlisted_params(body, VLLM) == ["min_p", "venice_parameters"]
    assert up.binding_roots(VENICE) == frozenset({"venice_parameters"})
    assert up.binding_roots({"bindings": {"x": {"kind": "body_param", "param": 5}, "y": "junk", "z": {"param": ""}}}) \
        == frozenset()


def test_anthropic_stack_is_skipped_entirely():
    body = {**STANDARD_BODY, "min_p": 0.1, "stop_sequences": ["x"], "system": "s"}
    assert up.unlisted_params(body, ANTHROPIC) == []
    assert up.unlisted_headers(body, ANTHROPIC) == {}


def test_report_only_never_touches_the_body():
    body = {**STANDARD_BODY, "min_p": 0.1}
    before = dict(body)
    up.unlisted_headers(body, VLLM)
    assert body == before


def test_headers_sanitize_untrusted_keys():
    body = {**STANDARD_BODY, "bad\r\nX-Injected: 1": 1, "日本語": 2, "ok_key": 3}
    assert up.unlisted_headers(body, VLLM) == {"X-Morpheus-Unlisted-Params": "badX-Injected1;ok_key"}


async def test_prepare_translation_headers_reports_unlisted_params_from_the_full_body():
    """The preview passed to translate_for_session carries only translatable fields; the check must see the whole body."""
    request = MagicMock(headers={"X-Morpheus-Translate": "1"})
    json_body = {"messages": [], "max_tokens": 5, "min_p": 0.1, "chat_template_kwargs": {"enable_thinking": True}}
    row = MagicMock()
    row.provider_address = "0xprov"

    class _Db:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            return False

    with patch.object(chat_index.settings, "REQUEST_TRANSLATION_MODE", "header"), \
         patch.object(chat_index.settings, "REQUEST_TRANSLATION_HEADER", "X-Morpheus-Translate"), \
         patch.object(rt, "get_db", lambda: _Db()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=row), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=VLLM):
        headers = await chat_index._prepare_translation_headers(request, json_body, "0xsess", "0x01")

    assert headers["X-Morpheus-Unlisted-Params"] == "min_p"
    assert json_body["min_p"] == 0.1, "report only"


async def test_prepare_translation_headers_omits_header_without_spec():
    request = MagicMock(headers={"X-Morpheus-Translate": "1"})
    with patch.object(chat_index.settings, "REQUEST_TRANSLATION_MODE", "header"), \
         patch.object(chat_index.settings, "REQUEST_TRANSLATION_HEADER", "X-Morpheus-Translate"), \
         patch.object(chat_index, "translate_for_session", new_callable=AsyncMock, return_value=rt.TranslationResult()):
        headers = await chat_index._prepare_translation_headers(request, {"messages": [], "min_p": 0.1}, "0xsess", "0x01")
    assert "X-Morpheus-Unlisted-Params" not in headers


def test_cors_exposes_unlisted_params_header():
    exposed = [m.kwargs.get("expose_headers", []) for m in main_module.app.user_middleware]
    assert any("X-Morpheus-Unlisted-Params" in headers for headers in exposed)
