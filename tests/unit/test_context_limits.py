import contextlib
import copy
import json
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
from src import main as main_module  # noqa: E402
from src.services.proxy_router_service import ProxyRouterServiceError  # noqa: E402
from tests.unit.test_chat_translation_handlers import FakeStreamResponse, _stream_cm_factory  # noqa: E402

MESSAGES = [{"role": "user", "content": "hello there"}]

# maxOutput is below every client max_tokens in these tests: the gateway reports the window and never clamps to it
SPEC_VENICE = {
    "stack": "venice",
    "capabilities": {"context": 8192, "maxOutput": 1024},
    "parameters": ["model", "messages", "max_tokens", "max_completion_tokens"],
    "bindings": {"reasoning.disable": {"kind": "body_param", "param": "venice_parameters.disable_thinking", "paramType": "boolean", "value": True}},
}
SPEC_VLLM = {
    "stack": "vllm",
    "capabilities": {"context": 4096, "maxOutput": 512},
    "bindings": {"reasoning.disable": {"kind": "template_kwarg", "param": "chat_template_kwargs.enable_thinking", "paramType": "boolean", "value": False}},
}
PROVIDERS = {"0xsess": "0xAAAA", "0xold": "0xAAAA", "0xnew": "0xBBBB"}
SPECS = {"0xAAAA": SPEC_VENICE, "0xBBBB": SPEC_VLLM}


@pytest.fixture
def translation_enabled():
    token = rt.set_request_translation(True)
    try:
        yield
    finally:
        rt._TRANSLATION_ACTIVE.reset(token)
        rt.set_context_window(None)


def _fake_get_db():
    class FakeGetDb:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            return False

    return lambda: FakeGetDb()


@contextlib.contextmanager
def _provider_specs():
    """The real lookup path: each session resolves to its provider, each provider to its spec."""
    with patch.object(rt, "get_db", _fake_get_db()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock,
                      side_effect=lambda db, session_id: MagicMock(provider_address=PROVIDERS[session_id])) as gsi, \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock,
                      side_effect=lambda address, model_id: SPECS[address]) as gs:
        yield gsi, gs


def test_read_context_accepts_only_positive_ints():
    assert cl.read_context(SPEC_VENICE) == 8192
    assert cl.read_context(None) is None
    assert cl.read_context({"stack": "vllm"}) is None
    assert cl.read_context({"capabilities": "junk"}) is None
    assert cl.read_context({"capabilities": {"maxOutput": 1024}}) is None
    assert cl.read_context({"capabilities": {"context": 0}}) is None
    assert cl.read_context({"capabilities": {"context": True}}) is None
    assert cl.read_context({"capabilities": {"context": "8192"}}) is None
    assert cl.read_context({"capabilities": {"context": 1 << 40}}) is None


def test_needs_translation_ignores_max_tokens():
    assert rt.needs_translation({"max_tokens": 1}) is False
    assert rt.needs_translation({"max_completion_tokens": 1, "temperature": 0.1}) is False
    assert rt.needs_translation({"reasoning_effort": "low"}) is True
    assert rt.needs_translation({"web_search": {"enabled": False}}) is True
    assert rt.needs_translation({"web_search_options": {}}) is True


@pytest.mark.parametrize("body", [{"max_tokens": 9000}, {"max_completion_tokens": 9000, "temperature": 0.2}])
async def test_translate_for_session_max_tokens_alone_makes_no_lookup(translation_enabled, body):
    sent = copy.deepcopy(body)
    with _provider_specs() as (gsi, gs):
        result = await rt.translate_for_session(body, "0xsess", "0x01")
    gsi.assert_not_awaited()
    gs.assert_not_awaited()
    assert body == sent
    assert result.headers() == {}
    assert "X-Morpheus-Context-Window" not in rt.translation_gate_headers()


async def test_translate_for_session_never_changes_max_tokens(translation_enabled):
    body = {"reasoning": {"enabled": False}, "max_tokens": 9000, "max_completion_tokens": 5000}
    with _provider_specs() as (_, gs):
        result = await rt.translate_for_session(body, "0xsess", "0x01")
    gs.assert_awaited_once_with("0xAAAA", "0x01")
    assert body == {"venice_parameters": {"disable_thinking": True}, "max_tokens": 9000, "max_completion_tokens": 5000}, \
        "maxOutput 1024 is reported by /models, never applied to the request"
    assert result.headers() == {"X-Morpheus-Provider-Stack": "venice",
                                "X-Morpheus-Translated": "reasoning.disable=venice_parameters.disable_thinking",
                                "X-Morpheus-Context-Window": "8192"}
    assert rt.translation_gate_headers()["X-Morpheus-Context-Window"] == "8192"


async def test_translate_for_session_spec_without_context_reports_no_window(translation_enabled):
    rt.set_context_window(4096)  # left by an earlier attempt's provider
    with patch.object(rt, "get_db", _fake_get_db()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=MagicMock(provider_address="0xAAAA")), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock, return_value={"stack": "ollama", "capabilities": {"maxOutput": 1024}}):
        result = await rt.translate_for_session({"reasoning": {"enabled": False}}, "0xsess", "0x01")
    assert result.context_window is None
    assert result.headers()["X-Morpheus-Provider-Stack"] == "ollama"
    assert "X-Morpheus-Context-Window" not in result.headers()
    assert "X-Morpheus-Context-Window" not in rt.translation_gate_headers()


async def test_translate_for_session_reports_window_when_nothing_applies(translation_enabled):
    body = {"reasoning": {"enabled": False}}
    with patch.object(rt, "get_db", _fake_get_db()), \
         patch.object(rt.session_routing_service, "get_session_info", new_callable=AsyncMock, return_value=MagicMock(provider_address="0xAAAA")), \
         patch.object(rt.provider_api_spec_service, "get_spec", new_callable=AsyncMock,
                      return_value={"stack": "ollama", "capabilities": {"context": 8192}, "bindings": {}}):
        result = await rt.translate_for_session(body, "0xsess", "0x01")
    assert result.touched is False
    assert body == {"reasoning": {"enabled": False}}
    assert result.headers()["X-Morpheus-Context-Window"] == "8192"
    assert rt.translation_gate_headers()["X-Morpheus-Context-Window"] == "8192"


async def test_translate_for_session_logs_without_clamp_fields(translation_enabled):
    with patch.object(rt, "logger") as logger, _provider_specs():
        await rt.translate_for_session({"reasoning": {"enabled": False}, "max_tokens": 9000}, "0xsess", "0x01")
    applied = [c for c in logger.info.call_args_list if c.kwargs.get("event_type") == "request_translation_applied"]
    assert len(applied) == 1
    fields = applied[0].kwargs
    assert fields["context_window"] == 8192
    assert fields["applied"] == {"reasoning.disable": "venice_parameters.disable_thinking"}
    assert not {"clamped", "max_output", "estimated_prompt_tokens"} & fields.keys()


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


async def test_prepare_translation_headers_max_tokens_alone_makes_no_lookup():
    request = MagicMock(headers={"X-Morpheus-Translate": "1"})
    json_body = {"messages": MESSAGES, "max_tokens": 9000, "max_completion_tokens": 9000, "min_p": 0.1}
    sent = copy.deepcopy(json_body)
    with patch.object(chat_index.settings, "REQUEST_TRANSLATION_MODE", "header"), \
         patch.object(chat_index.settings, "REQUEST_TRANSLATION_HEADER", "X-Morpheus-Translate"), \
         _provider_specs() as (gsi, gs):
        headers = await chat_index._prepare_translation_headers(request, json_body, "0xsess", "0x01")

    gsi.assert_not_awaited()
    gs.assert_not_awaited()
    assert headers == {"X-Morpheus-Translation": "on", "X-Morpheus-Translation-Mode": "header"}, \
        "no Provider-Stack, Context-Window or Unlisted-Params without a lookup"
    assert json_body == sent


async def test_prepare_translation_headers_reports_context_window_for_a_translated_request():
    request = MagicMock(headers={"X-Morpheus-Translate": "1"})
    json_body = {"messages": MESSAGES, "reasoning": {"enabled": False}, "max_tokens": 9000}
    with patch.object(chat_index.settings, "REQUEST_TRANSLATION_MODE", "header"), \
         patch.object(chat_index.settings, "REQUEST_TRANSLATION_HEADER", "X-Morpheus-Translate"), \
         _provider_specs() as (_, gs):
        headers = await chat_index._prepare_translation_headers(request, json_body, "0xsess", "0x01")
        # A later failure of the same request carries the window through the request context
        error = await main_module.chat_error_handler(MagicMock(), chat_index.GatewayError(message="boom"))
    rt.set_context_window(None)

    gs.assert_awaited_once()
    assert headers == {"X-Morpheus-Translation": "on", "X-Morpheus-Translation-Mode": "header",
                       "X-Morpheus-Provider-Stack": "venice",
                       "X-Morpheus-Translated": "reasoning.disable=venice_parameters.disable_thinking",
                       "X-Morpheus-Context-Window": "8192"}
    assert error.headers["X-Morpheus-Context-Window"] == "8192"
    assert json_body["max_tokens"] == 9000, "the preview must not mutate the request body"


async def test_prepare_translation_headers_inactive_never_translates():
    request = MagicMock(headers={})
    with patch.object(chat_index.settings, "REQUEST_TRANSLATION_MODE", "header"), \
         patch.object(chat_index.settings, "REQUEST_TRANSLATION_HEADER", "X-Morpheus-Translate"), \
         patch.object(chat_index, "translate_for_session", new_callable=AsyncMock) as tfs:
        headers = await chat_index._prepare_translation_headers(request, {"messages": MESSAGES, "reasoning": {"enabled": False}}, "0xsess", "0x01")
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


@pytest.mark.parametrize("handler", [chat_non_streaming, chat_streaming])
async def test_wire_params_forwards_max_tokens_alone_without_lookup(translation_enabled, handler):
    chat_params = {"max_tokens": 9000, "max_completion_tokens": 9000, "temperature": 0.2}
    with _provider_specs() as (gsi, gs):
        params = await handler.wire_params(chat_params, "0xsess", "0x01")
    gsi.assert_not_awaited()
    gs.assert_not_awaited()
    assert params == {"max_tokens": 9000, "max_completion_tokens": 9000, "temperature": 0.2}


PROVIDER_DOWN = ProxyRouterServiceError(
    'HTTP 500: {"error":"provider request failed: failed to connect to provider: connection refused"}',
    status_code=500,
    error_type="server_error",
)
BODY = json.dumps({"messages": MESSAGES, "reasoning": {"enabled": False}, "max_tokens": 9000,
                   "max_completion_tokens": 9000}).encode()


async def test_non_streaming_failover_never_changes_max_tokens(translation_enabled):
    ok = httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    with patch.object(chat_non_streaming.proxy_router_service, "chatCompletions", new_callable=AsyncMock,
                      side_effect=[PROVIDER_DOWN, ok]) as chat, \
         patch.object(chat_non_streaming.chat_failover, "attempt_failover", new_callable=AsyncMock, return_value="0xnew"), \
         patch.object(chat_non_streaming, "get_db", _fake_get_db()), \
         _provider_specs() as (_, gs):
        await chat_non_streaming.handle_non_streaming_request(
            logger=MagicMock(),
            request_id="req-1",
            body=BODY,
            db_api_key=MagicMock(),
            user=MagicMock(id=42),
            requested_model="llama-3.3-70b",
            model_id="0x01",
            session_id="0xold",
        )

    assert gs.await_count == 2, "each attempt translates against its own provider"
    first, second = chat.await_args_list[0].kwargs, chat.await_args_list[1].kwargs
    assert first["session_id"] == "0xold" and first["venice_parameters"] == {"disable_thinking": True}
    assert second["session_id"] == "0xnew" and second["chat_template_kwargs"] == {"enable_thinking": False}
    assert first["max_tokens"] == second["max_tokens"] == 9000
    assert first["max_completion_tokens"] == second["max_completion_tokens"] == 9000
    assert second["messages"] == MESSAGES


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


SESSION_EXPIRED = ProxyRouterServiceError('HTTP 500: {"error":"session expired"}', status_code=500, error_type="server_error")
GOOD_CHUNKS = [b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', b"data: [DONE]\n\n"]


async def test_streaming_session_renewal_never_changes_max_tokens(translation_enabled):
    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      _stream_cm_factory([SESSION_EXPIRED, FakeStreamResponse(GOOD_CHUNKS)])) as stream, \
         patch.object(chat_streaming.chat_failover, "attempt_failover", new_callable=AsyncMock) as failover, \
         patch.object(chat_streaming.session_routing_service, "invalidate_session", new_callable=AsyncMock, return_value=True), \
         patch.object(chat_streaming.session_routing_service, "route_request", new_callable=AsyncMock, return_value="0xnew"), \
         patch.object(chat_streaming.session_routing_service, "release_session", new_callable=AsyncMock), \
         patch.object(chat_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock), \
         _provider_specs(), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        async for _ in _stream_generator(BODY)():
            pass

    failover.assert_not_awaited()
    first, second = stream.calls[0], stream.calls[1]
    assert first["session_id"] == "0xold" and first["venice_parameters"] == {"disable_thinking": True}
    assert second["session_id"] == "0xnew" and second["chat_template_kwargs"] == {"enable_thinking": False}
    assert first["max_tokens"] == second["max_tokens"] == 9000
    assert first["max_completion_tokens"] == second["max_completion_tokens"] == 9000


async def test_streaming_failover_never_changes_max_tokens(translation_enabled):
    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      _stream_cm_factory([PROVIDER_DOWN, FakeStreamResponse(GOOD_CHUNKS)])) as stream, \
         patch.object(chat_streaming.chat_failover, "attempt_failover", new_callable=AsyncMock, return_value="0xnew") as failover, \
         patch.object(chat_streaming.session_routing_service, "release_session", new_callable=AsyncMock), \
         patch.object(chat_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock), \
         _provider_specs():
        async for _ in _stream_generator(BODY)():
            pass

    failover.assert_awaited_once()
    first, second = stream.calls[0], stream.calls[1]
    assert first["session_id"] == "0xold" and first["venice_parameters"] == {"disable_thinking": True}
    assert second["session_id"] == "0xnew" and second["chat_template_kwargs"] == {"enable_thinking": False}
    assert first["max_tokens"] == second["max_tokens"] == 9000
    assert first["max_completion_tokens"] == second["max_completion_tokens"] == 9000
