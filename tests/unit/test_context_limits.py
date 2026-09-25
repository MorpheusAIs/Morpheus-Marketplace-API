import json
import math
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.api.v1.chat import context_limits as cl  # noqa: E402
from src.api.v1.chat import request_translation as rt  # noqa: E402
from src.api.v1.chat import index as chat_index  # noqa: E402
from src.api.v1.chat import chat_non_streaming, chat_streaming  # noqa: E402
from src.api.v1.chat.chat_exceptions import ContextLengthError  # noqa: E402
from src import main as main_module  # noqa: E402
from src.services.proxy_router_service import ProxyRouterServiceError  # noqa: E402
from tests.unit.test_chat_translation_handlers import FakeStreamResponse, _stream_cm_factory  # noqa: E402

MESSAGES = [{"role": "user", "content": "hello there"}]
ESTIMATE = math.ceil(len(json.dumps(MESSAGES, ensure_ascii=False)) / 4)

SPEC_BOTH = {"stack": "vllm", "capabilities": {"context": 8192, "maxOutput": 1024}}
SPEC_CONTEXT_ONLY = {"stack": "vllm", "capabilities": {"context": 100}}
SPEC_REASONING = {
    "stack": "venice",
    "capabilities": {"context": 8192, "maxOutput": 1024},
    "bindings": {"reasoning.disable": {"kind": "body_param", "param": "venice_parameters.disable_thinking", "paramType": "boolean", "value": True}},
}


@pytest.fixture
def translation_enabled():
    token = rt.set_request_translation(True)
    try:
        yield
    finally:
        rt._TRANSLATION_ACTIVE.reset(token)
        rt.set_context_window(None)


def test_estimate_prompt_tokens_is_ceil_of_json_length_over_four():
    assert cl.estimate_prompt_tokens(MESSAGES) == ESTIMATE
    assert cl.estimate_prompt_tokens([]) == 1
    assert cl.estimate_prompt_tokens([{"role": "user", "content": "日本語"}]) == math.ceil(len('[{"role": "user", "content": "日本語"}]') / 4)


def test_read_limits_accepts_only_positive_ints():
    assert cl.read_limits(SPEC_BOTH) == (8192, 1024)
    assert cl.read_limits(SPEC_CONTEXT_ONLY) == (100, None)
    assert cl.read_limits(None) == (None, None)
    assert cl.read_limits({"stack": "vllm"}) == (None, None)
    assert cl.read_limits({"capabilities": "junk"}) == (None, None)
    assert cl.read_limits({"capabilities": {"context": 0, "maxOutput": -5}}) == (None, None)
    assert cl.read_limits({"capabilities": {"context": True, "maxOutput": "1024"}}) == (None, None)
    assert cl.read_limits({"capabilities": {"context": 1 << 40}}) == (None, None)


def test_clamps_max_tokens_and_max_completion_tokens_to_max_output():
    body = {"max_tokens": 4096, "max_completion_tokens": 2000, "temperature": 0.1}
    result = cl.apply_context_limits(body, SPEC_BOTH, MESSAGES)
    assert body == {"max_tokens": 1024, "max_completion_tokens": 1024, "temperature": 0.1}
    assert result.touched is True
    assert result.clamped == [("max_tokens", 4096, 1024), ("max_completion_tokens", 2000, 1024)]
    assert result.headers() == {"X-Morpheus-Context-Window": "8192", "X-Morpheus-Max-Tokens-Clamped": "4096->1024;2000->1024"}


def test_values_at_or_below_max_output_and_non_ints_are_left_alone():
    body = {"max_tokens": 1024, "max_completion_tokens": "2000"}
    result = cl.apply_context_limits(body, SPEC_BOTH, MESSAGES)
    assert body == {"max_tokens": 1024, "max_completion_tokens": "2000"}
    assert result.touched is False and result.clamped == []
    assert result.headers() == {"X-Morpheus-Context-Window": "8192"}

    untouched = {"max_tokens": True}
    cl.apply_context_limits(untouched, SPEC_BOTH, MESSAGES)
    assert untouched == {"max_tokens": True}


def test_clamps_to_context_minus_estimate_when_max_output_absent():
    body = {"max_tokens": 4096}
    result = cl.apply_context_limits(body, SPEC_CONTEXT_ONLY, MESSAGES)
    assert body["max_tokens"] == 100 - ESTIMATE
    assert result.clamped == [("max_tokens", 4096, 100 - ESTIMATE)]
    assert result.headers()["X-Morpheus-Max-Tokens-Clamped"] == f"4096->{100 - ESTIMATE}"


def test_context_only_clamp_needs_at_least_one_token_left():
    spec = {"capabilities": {"context": ESTIMATE}}
    body = {"max_tokens": 4096}
    result = cl.apply_context_limits(body, spec, MESSAGES)
    assert body == {"max_tokens": 4096}
    assert result.touched is False and result.prompt_exceeds is False
    assert result.headers() == {"X-Morpheus-Context-Window": str(ESTIMATE)}

    spec = {"capabilities": {"context": ESTIMATE + 1}}
    body = {"max_tokens": 4096}
    cl.apply_context_limits(body, spec, MESSAGES)
    assert body == {"max_tokens": 1}


def test_context_only_clamp_skipped_without_messages():
    body = {"max_tokens": 4096}
    result = cl.apply_context_limits(body, SPEC_CONTEXT_ONLY, None)
    assert body == {"max_tokens": 4096}
    assert result.estimated_prompt_tokens is None and result.touched is False


def test_prompt_exceeding_context_is_reported_and_max_output_still_clamps():
    spec = {"capabilities": {"context": ESTIMATE - 1, "maxOutput": 5}}
    body = {"max_tokens": 4096}
    result = cl.apply_context_limits(body, spec, MESSAGES)
    assert result.prompt_exceeds is True
    assert result.estimated_prompt_tokens == ESTIMATE
    assert body == {"max_tokens": 5}, "a retry that forwards the body anyway must respect maxOutput"
    assert result.clamped == [("max_tokens", 4096, 5)]
    assert result.headers() == {"X-Morpheus-Context-Window": str(ESTIMATE - 1), "X-Morpheus-Max-Tokens-Clamped": "4096->5"}


def test_prompt_exceeding_context_without_max_output_is_not_rewritten():
    spec = {"capabilities": {"context": ESTIMATE - 1}}
    body = {"max_tokens": 4096}
    result = cl.apply_context_limits(body, spec, MESSAGES)
    assert result.prompt_exceeds is True
    assert body == {"max_tokens": 4096}, "context minus an oversize prompt is no ceiling"
    assert result.touched is False and result.clamped == []


def test_no_capabilities_is_a_noop():
    body = {"max_tokens": 4096}
    result = cl.apply_context_limits(body, {"stack": "vllm"}, MESSAGES)
    assert body == {"max_tokens": 4096}
    assert result == cl.ContextLimitResult()
    assert result.headers() == {}


def test_context_length_error_response_shape_and_headers():
    err = ContextLengthError(context_window=4096, estimated_prompt_tokens=5000)
    response = err.to_response()
    assert response.status_code == 400
    body = json.loads(response.body)
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "context_length_exceeded"
    assert body["error"]["context_window"] == 4096
    assert body["error"]["estimated_prompt_tokens"] == 5000
    assert "4096" in body["error"]["message"] and "5000" in body["error"]["message"]
    assert response.headers["X-Morpheus-Context-Window"] == "4096"
    assert response.headers["X-Morpheus-Error"] == "context_length_exceeded"


def _fake_get_db():
    class FakeGetDb:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            return False

    return lambda: FakeGetDb()


def _row():
    return MagicMock(provider_address="0xAAAA")


async def test_translate_for_session_clamps_without_reasoning_intents(translation_enabled):
    body = {"max_tokens": 9000, "temperature": 0.2}
    with patch.object(rt, "get_db", _fake_get_db()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=_row()), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=SPEC_BOTH):
        result = await rt.translate_for_session(body, "0xsess", "0x01", messages=MESSAGES)

    assert body == {"max_tokens": 1024, "temperature": 0.2}
    assert result.touched is True
    assert result.applied == {} and result.unsupported == []
    assert result.headers() == {"X-Morpheus-Provider-Stack": "vllm", "X-Morpheus-Context-Window": "8192",
                                "X-Morpheus-Max-Tokens-Clamped": "9000->1024"}, \
        "a found spec names its stack even when only the clamp ran"
    assert rt._CONTEXT_WINDOW.get() == 8192
    assert rt.translation_gate_headers()["X-Morpheus-Context-Window"] == "8192"


async def test_translate_for_session_reasoning_and_clamp_together(translation_enabled):
    body = {"reasoning": {"enabled": False}, "max_completion_tokens": 5000}
    with patch.object(rt, "get_db", _fake_get_db()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=_row()), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=SPEC_REASONING):
        result = await rt.translate_for_session(body, "0xsess", "0x01", messages=MESSAGES)

    assert body == {"venice_parameters": {"disable_thinking": True}, "max_completion_tokens": 1024}
    headers = result.headers()
    assert headers["X-Morpheus-Translated"] == "reasoning.disable=venice_parameters.disable_thinking"
    assert headers["X-Morpheus-Max-Tokens-Clamped"] == "5000->1024"
    assert headers["X-Morpheus-Context-Window"] == "8192"


async def test_translate_for_session_reasoning_only_body_is_byte_identical_to_before(translation_enabled):
    body = {"reasoning": {"enabled": False}, "temperature": 0.5}
    with patch.object(rt, "get_db", _fake_get_db()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=_row()), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=SPEC_REASONING):
        result = await rt.translate_for_session(body, "0xsess", "0x01")

    assert body == {"venice_parameters": {"disable_thinking": True}, "temperature": 0.5}
    assert result.context_limits.clamped == [] and result.context_limits.estimated_prompt_tokens is None
    assert result.headers()["X-Morpheus-Context-Window"] == "8192"


async def test_translate_for_session_reports_prompt_exceeding_context(translation_enabled):
    spec = {"capabilities": {"context": ESTIMATE - 1}}
    body = {"max_tokens": 50}
    with patch.object(rt, "get_db", _fake_get_db()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=_row()), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=spec):
        result = await rt.translate_for_session(body, "0xsess", "0x01", messages=MESSAGES)

    assert result.context_limits.prompt_exceeds is True
    assert body == {"max_tokens": 50}, "an oversize prompt is reported; without maxOutput there is nothing to clamp to"


async def test_translate_for_session_plain_body_without_messages_skips_lookups(translation_enabled):
    with patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock) as gsi, \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock) as gs:
        result = await rt.translate_for_session({"temperature": 0.1}, "0xsess", "0x01")
    gsi.assert_not_awaited()
    gs.assert_not_awaited()
    assert result.context_limits is None


async def test_translate_for_session_max_tokens_alone_triggers_lookup(translation_enabled):
    body = {"max_tokens": 9000}
    with patch.object(rt, "get_db", _fake_get_db()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=_row()), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=SPEC_BOTH) as gs:
        await rt.translate_for_session(body, "0xsess", "0x01")
    gs.assert_awaited_once()
    assert body == {"max_tokens": 1024}


def test_needs_translation():
    assert rt.needs_translation({"max_tokens": 1}) is True
    assert rt.needs_translation({"max_completion_tokens": 1}) is True
    assert rt.needs_translation({"reasoning_effort": "low"}) is True
    assert rt.needs_translation({"temperature": 0.1}) is False


def test_set_request_translation_resets_context_window():
    token = rt.set_request_translation(True)
    try:
        rt.set_context_window(4096)
        assert rt.translation_gate_headers().get("X-Morpheus-Context-Window") == "4096"
        rt.set_request_translation(True)
        assert "X-Morpheus-Context-Window" not in rt.translation_gate_headers()
    finally:
        rt._TRANSLATION_ACTIVE.reset(token)
        rt.set_context_window(None)


async def test_prepare_translation_headers_carries_context_headers_and_previews_max_tokens():
    request = MagicMock(headers={"X-Morpheus-Translate": "1"})
    json_body = {"messages": MESSAGES, "max_tokens": 9000, "temperature": 0.3}
    captured = {}

    async def fake(body, session_id, model_id, messages=None):
        captured["body"] = dict(body)
        captured["messages"] = messages
        result = rt.apply_spec(body, SPEC_BOTH)
        result.context_limits = cl.apply_context_limits(body, SPEC_BOTH, messages)
        rt.set_context_window(result.context_limits.context)
        return result

    with patch.object(chat_index.settings, "REQUEST_TRANSLATION_MODE", "header"), \
         patch.object(chat_index.settings, "REQUEST_TRANSLATION_HEADER", "X-Morpheus-Translate"), \
         patch.object(chat_index, "translate_for_session", fake):
        headers = await chat_index._prepare_translation_headers(request, json_body, "0xsess", "0x01")

    assert captured["body"] == {"max_tokens": 9000}, "preview carries only the translatable fields"
    assert captured["messages"] == MESSAGES
    assert headers["X-Morpheus-Context-Window"] == "8192"
    assert headers["X-Morpheus-Max-Tokens-Clamped"] == "9000->1024"
    assert headers["X-Morpheus-Translation"] == "on"
    assert json_body["max_tokens"] == 9000, "the preview must not mutate the request body"
    rt.set_context_window(None)


async def test_prepare_translation_headers_raises_400_when_prompt_exceeds_context():
    request = MagicMock(headers={"X-Morpheus-Translate": "1"})
    spec = {"capabilities": {"context": ESTIMATE - 1}}

    async def fake(body, session_id, model_id, messages=None):
        result = rt.TranslationResult()
        result.context_limits = cl.apply_context_limits(body, spec, messages)
        return result

    with patch.object(chat_index.settings, "REQUEST_TRANSLATION_MODE", "header"), \
         patch.object(chat_index.settings, "REQUEST_TRANSLATION_HEADER", "X-Morpheus-Translate"), \
         patch.object(chat_index, "translate_for_session", fake):
        with pytest.raises(ContextLengthError) as excinfo:
            await chat_index._prepare_translation_headers(request, {"messages": MESSAGES}, "0xsess", "0x01")

    assert excinfo.value.context_window == ESTIMATE - 1
    assert excinfo.value.estimated_prompt_tokens == ESTIMATE
    assert excinfo.value.status_code == 400


async def test_prepare_translation_headers_inactive_never_translates():
    request = MagicMock(headers={})
    with patch.object(chat_index.settings, "REQUEST_TRANSLATION_MODE", "header"), \
         patch.object(chat_index.settings, "REQUEST_TRANSLATION_HEADER", "X-Morpheus-Translate"), \
         patch.object(chat_index, "translate_for_session", new_callable=AsyncMock) as tfs:
        headers = await chat_index._prepare_translation_headers(request, {"messages": MESSAGES, "max_tokens": 10 ** 9}, "0xsess", "0x01")
    tfs.assert_not_awaited()
    assert "X-Morpheus-Context-Window" not in headers


async def test_chat_error_handler_adds_context_window_from_request_context():
    token = rt.set_request_translation(True)
    try:
        rt.set_context_window(4096)
        with patch.object(main_module.settings, "REQUEST_TRANSLATION_MODE", "header"):
            response = await main_module.chat_error_handler(MagicMock(), chat_index.GatewayError(message="boom"))
    finally:
        rt._TRANSLATION_ACTIVE.reset(token)
        rt.set_context_window(None)
    assert response.headers["X-Morpheus-Context-Window"] == "4096"
    assert response.headers["X-Morpheus-Translation"] == "on"


def test_cors_exposes_context_headers():
    with open(main_module.__file__, encoding="utf-8") as fh:
        source = fh.read()
    assert '"X-Morpheus-Context-Window"' in source
    assert '"X-Morpheus-Max-Tokens-Clamped"' in source


def _spec_by_session(mapping):
    async def fake(body, session_id, model_id, messages=None):
        result = rt.apply_spec(body, mapping.get(session_id))
        result.context_limits = cl.apply_context_limits(body, mapping.get(session_id), messages)
        return result
    return fake


PROVIDER_DOWN = ProxyRouterServiceError(
    'HTTP 500: {"error":"provider request failed: failed to connect to provider: connection refused"}',
    status_code=500,
    error_type="server_error",
)


async def test_non_streaming_reclamps_per_attempt_against_the_new_provider_spec():
    body = json.dumps({"messages": MESSAGES, "max_tokens": 9000}).encode()
    specs = {"0xold": {"capabilities": {"context": 8192, "maxOutput": 1024}}, "0xnew": {"capabilities": {"context": 8192, "maxOutput": 2048}}}
    ok = httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    with patch.object(chat_non_streaming.proxy_router_service, "chatCompletions", new_callable=AsyncMock,
                      side_effect=[PROVIDER_DOWN, ok]) as chat, \
         patch.object(chat_non_streaming.chat_failover, "attempt_failover", new_callable=AsyncMock, return_value="0xnew"), \
         patch.object(chat_non_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_non_streaming, "translate_for_session", _spec_by_session(specs)):
        await chat_non_streaming.handle_non_streaming_request(
            logger=MagicMock(),
            request_id="req-1",
            body=body,
            db_api_key=MagicMock(),
            user=MagicMock(id=42),
            requested_model="llama-3.3-70b",
            model_id="0x01",
            session_id="0xold",
        )

    first, second = chat.await_args_list[0].kwargs, chat.await_args_list[1].kwargs
    assert first["session_id"] == "0xold" and first["max_tokens"] == 1024
    assert second["session_id"] == "0xnew" and second["max_tokens"] == 2048, "retry clamps the canonical value, not the previous clamp"
    assert second["messages"] == MESSAGES


async def test_non_streaming_retry_against_an_oversize_context_still_clamps_to_max_output():
    body = json.dumps({"messages": MESSAGES, "max_tokens": 9000}).encode()
    specs = {"0xold": {"capabilities": {"context": 8192, "maxOutput": 1024}},
             "0xnew": {"capabilities": {"context": ESTIMATE - 1, "maxOutput": 512}}}
    ok = httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    with patch.object(chat_non_streaming.proxy_router_service, "chatCompletions", new_callable=AsyncMock,
                      side_effect=[PROVIDER_DOWN, ok]) as chat, \
         patch.object(chat_non_streaming.chat_failover, "attempt_failover", new_callable=AsyncMock, return_value="0xnew"), \
         patch.object(chat_non_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_non_streaming, "translate_for_session", _spec_by_session(specs)):
        response = await chat_non_streaming.handle_non_streaming_request(
            logger=MagicMock(),
            request_id="req-1",
            body=body,
            db_api_key=MagicMock(),
            user=MagicMock(id=42),
            requested_model="llama-3.3-70b",
            model_id="0x01",
            session_id="0xold",
        )

    assert response.status_code == 200
    second = chat.await_args_list[1].kwargs
    assert second["session_id"] == "0xnew" and second["messages"] == MESSAGES
    assert second["max_tokens"] == 512, "the oversize prompt is the provider's call; the output cap is still ours"


def _stream_generator(body: bytes):
    return chat_streaming.build_stream_generator(
        logger=MagicMock(),
        session_id="0xold",
        body=body,
        requested_model="llama-3.3-70b",
        model_id="0x01",
        db_api_key=MagicMock(),
        user=MagicMock(id=42),
    )


STREAM_BODY = json.dumps({"messages": MESSAGES, "max_tokens": 9000}).encode()
# Context-only specs: the clamp is context minus the prompt estimate, so it only happens when messages are threaded
STREAM_SPECS = {"0xold": {"capabilities": {"context": 1024 + ESTIMATE}},
                "0xnew": {"capabilities": {"context": 2048 + ESTIMATE}}}
SESSION_EXPIRED = ProxyRouterServiceError('HTTP 500: {"error":"session expired"}', status_code=500, error_type="server_error")
GOOD_CHUNKS = [b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', b"data: [DONE]\n\n"]


async def test_streaming_clamps_initial_attempt_and_threads_messages():
    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      _stream_cm_factory([FakeStreamResponse(GOOD_CHUNKS)])) as stream, \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock), \
         patch.object(chat_streaming, "translate_for_session", _spec_by_session(STREAM_SPECS)):
        async for _ in _stream_generator(STREAM_BODY)():
            pass

    kwargs = stream.calls[0]
    assert kwargs["session_id"] == "0xold" and kwargs["max_tokens"] == 1024
    assert kwargs["messages"] == MESSAGES


async def test_streaming_reclamps_on_session_renewal_retry():
    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      _stream_cm_factory([SESSION_EXPIRED, FakeStreamResponse(GOOD_CHUNKS)])) as stream, \
         patch.object(chat_streaming.chat_failover, "attempt_failover", new_callable=AsyncMock) as failover, \
         patch.object(chat_streaming.session_routing_service, "invalidate_session", new_callable=AsyncMock, return_value=True), \
         patch.object(chat_streaming.session_routing_service, "route_request", new_callable=AsyncMock, return_value="0xnew"), \
         patch.object(chat_streaming.session_routing_service, "release_session", new_callable=AsyncMock), \
         patch.object(chat_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock), \
         patch.object(chat_streaming, "translate_for_session", _spec_by_session(STREAM_SPECS)), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        async for _ in _stream_generator(STREAM_BODY)():
            pass

    failover.assert_not_awaited()
    first, second = stream.calls[0], stream.calls[1]
    assert first["session_id"] == "0xold" and first["max_tokens"] == 1024
    assert second["session_id"] == "0xnew" and second["max_tokens"] == 2048, "retry clamps the canonical value, not the previous clamp"
    assert second["messages"] == MESSAGES


async def test_streaming_reclamps_on_failover_retry():
    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      _stream_cm_factory([PROVIDER_DOWN, FakeStreamResponse(GOOD_CHUNKS)])) as stream, \
         patch.object(chat_streaming.chat_failover, "attempt_failover", new_callable=AsyncMock, return_value="0xnew") as failover, \
         patch.object(chat_streaming.session_routing_service, "release_session", new_callable=AsyncMock), \
         patch.object(chat_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock), \
         patch.object(chat_streaming, "translate_for_session", _spec_by_session(STREAM_SPECS)):
        async for _ in _stream_generator(STREAM_BODY)():
            pass

    failover.assert_awaited_once()
    first, second = stream.calls[0], stream.calls[1]
    assert first["session_id"] == "0xold" and first["max_tokens"] == 1024
    assert second["session_id"] == "0xnew" and second["max_tokens"] == 2048
    assert second["messages"] == MESSAGES


def test_client_values_outside_the_wire_range_are_left_to_the_provider():
    huge = 10 ** 4000  # far past the wire range and past what str()/JSON would even render
    body = {"max_tokens": huge, "max_completion_tokens": 1 << 31}
    result = cl.apply_context_limits(body, SPEC_BOTH, MESSAGES)
    assert body == {"max_tokens": huge, "max_completion_tokens": 1 << 31}
    assert result.touched is False and result.clamped == []
    assert result.headers() == {"X-Morpheus-Context-Window": "8192"}

    body = {"max_tokens": (1 << 31) - 1}
    cl.apply_context_limits(body, SPEC_BOTH, MESSAGES)
    assert body == {"max_tokens": 1024}, "the largest wire int still clamps"


async def test_translate_for_session_spec_without_limits_still_names_the_stack(translation_enabled):
    body = {"max_tokens": 9000}
    with patch.object(rt, "get_db", _fake_get_db()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=_row()), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value={"stack": "ollama"}):
        result = await rt.translate_for_session(body, "0xsess", "0x01", messages=MESSAGES)
    assert result.headers() == {"X-Morpheus-Provider-Stack": "ollama"}
    with patch.object(rt, "get_db", _fake_get_db()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=_row()), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value=None):
        result = await rt.translate_for_session(body, "0xsess", "0x01", messages=MESSAGES)
    assert result.headers() == {}, "no spec, no stack"


async def test_create_chat_completion_voids_the_hold_and_releases_the_session_on_context_length_exceeded():
    from src.api.v1.chat.chat_models import ChatCompletionRequest

    request_data = ChatCompletionRequest.model_validate({"model": "m", "messages": MESSAGES, "max_tokens": 5})
    request = MagicMock(headers={"X-Morpheus-Translate": "1"})
    user = MagicMock(id=7)
    db_api_key = MagicMock(id=3)
    ledger_entry_id = object()

    async def fake_translate(body, session_id, model_id, messages=None):
        result = rt.TranslationResult()
        result.context_limits = cl.apply_context_limits(body, {"capabilities": {"context": ESTIMATE - 1}}, messages)
        return result

    with patch.object(chat_index.settings, "REQUEST_TRANSLATION_MODE", "header"), \
         patch.object(chat_index.settings, "REQUEST_TRANSLATION_HEADER", "X-Morpheus-Translate"), \
         patch.object(chat_index, "_check_rate_limits", new_callable=AsyncMock, return_value=MagicMock()), \
         patch.object(chat_index, "_create_billing_hold", new_callable=AsyncMock,
                      return_value=(ledger_entry_id, "0x01", 12, "real-model")), \
         patch.object(chat_index, "_resolve_session", new_callable=AsyncMock, return_value="0xsess"), \
         patch.object(chat_index, "_void_billing_hold", new_callable=AsyncMock) as void_hold, \
         patch.object(chat_index, "_release_session", new_callable=AsyncMock) as release, \
         patch.object(chat_index, "translate_for_session", fake_translate), \
         patch.object(chat_index, "_handle_streaming_request") as streaming, \
         patch.object(chat_index, "_handle_non_streaming_request", new_callable=AsyncMock) as non_streaming:
        with pytest.raises(ContextLengthError) as excinfo:
            await chat_index.create_chat_completion(request_data, request, user=user, db_api_key=db_api_key)

    assert excinfo.value.code == "context_length_exceeded"
    void_hold.assert_awaited_once()
    assert void_hold.await_args.kwargs["ledger_entry_id"] is ledger_entry_id
    assert void_hold.await_args.kwargs["user_id"] == 7
    assert void_hold.await_args.kwargs["failure_code"] == "context_length_exceeded"
    release.assert_awaited_once()
    assert release.await_args.args[0] == "0xsess"
    streaming.assert_not_called()
    non_streaming.assert_not_awaited()
    rt.set_context_window(None)
