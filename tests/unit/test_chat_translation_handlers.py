import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.api.v1.chat import chat_non_streaming, chat_streaming  # noqa: E402
from src.api.v1.chat import request_translation as rt  # noqa: E402
from src.services.proxy_router_service import ProxyRouterServiceError  # noqa: E402

VENICE = {"stack": "venice", "bindings": {"reasoning.disable": {"kind": "body_param", "param": "venice_parameters.disable_thinking", "paramType": "boolean", "value": True}}}
VLLM = {"stack": "vllm", "bindings": {"reasoning.disable": {"kind": "template_kwarg", "param": "chat_template_kwargs.enable_thinking", "paramType": "boolean", "value": False}}}
BODY = json.dumps({"messages": [{"role": "user", "content": "hi"}], "reasoning": {"enabled": False}, "request_id": "client-supplied"}).encode()

PROVIDER_DOWN = ProxyRouterServiceError(
    'HTTP 500: {"error":"provider request failed: failed to connect to provider: connection refused"}',
    status_code=500,
    error_type="server_error",
)
SESSION_EXPIRED = ProxyRouterServiceError(
    'HTTP 500: {"error":"session expired"}', status_code=500, error_type="server_error",
)
GOOD_CHUNKS = [b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', b"data: [DONE]\n\n"]


def _spec_by_session(mapping):
    async def fake(body, session_id, model_id):
        # No-op on a falsy model_id so an unthreaded model_id fails the assertions
        if not model_id:
            return rt.TranslationResult()
        return rt.apply_spec(body, mapping.get(session_id))
    return fake


def _fake_get_db():
    db = AsyncMock()

    class FakeGetDb:
        async def __aenter__(self):
            return db
        async def __aexit__(self, *args):
            return False

    return lambda: FakeGetDb()


def _success_response():
    return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = 42
    return user


class FakeStreamResponse:
    def __init__(self, chunks):
        self.status_code = 200
        self.headers = {}
        self._chunks = chunks

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class FakeStreamCM:
    def __init__(self, outcome):
        self._outcome = outcome

    async def __aenter__(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome

    async def __aexit__(self, *args):
        return False


def _stream_cm_factory(outcomes):
    it = iter(outcomes)
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return FakeStreamCM(next(it))

    factory.calls = calls
    return factory


def test_parsers_drop_client_request_id():
    _, params = chat_non_streaming._parse_request(BODY)
    assert "request_id" not in params
    _, params2 = chat_streaming._parse_request_body(BODY, MagicMock())
    assert "request_id" not in params2


async def test_non_streaming_translates_initial_and_failover_attempts(mock_user):
    with patch.object(chat_non_streaming.proxy_router_service, "chatCompletions", new_callable=AsyncMock,
                      side_effect=[PROVIDER_DOWN, _success_response()]) as chat, \
         patch.object(chat_non_streaming.chat_failover, "attempt_failover", new_callable=AsyncMock, return_value="0xnew"), \
         patch.object(chat_non_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_non_streaming, "translate_for_session", _spec_by_session({"0xold": VENICE, "0xnew": VLLM})):
        await chat_non_streaming.handle_non_streaming_request(
            logger=MagicMock(),
            request_id="req-1",
            body=BODY,
            db_api_key=MagicMock(),
            user=mock_user,
            requested_model="llama-3.3-70b",
            model_id="0x01",
            session_id="0xold",
        )

    first = chat.await_args_list[0].kwargs
    assert first["session_id"] == "0xold"
    assert first["venice_parameters"] == {"disable_thinking": True}
    assert "reasoning" not in first
    second = chat.await_args_list[1].kwargs
    assert second["session_id"] == "0xnew"
    assert second["chat_template_kwargs"] == {"enable_thinking": False}
    assert "venice_parameters" not in second, "retry must translate from the canonical params, not the previous wire params"


async def test_streaming_translates_initial_attempt(mock_user):
    ok_response = FakeStreamResponse([b"data: [DONE]\n\n"])
    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      _stream_cm_factory([ok_response])) as stream, \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock), \
         patch.object(chat_streaming, "translate_for_session", _spec_by_session({"0xold": VENICE})):
        gen = chat_streaming.build_stream_generator(
            logger=MagicMock(),
            session_id="0xold",
            body=BODY,
            requested_model="llama-3.3-70b",
            model_id="0x01",
            db_api_key=MagicMock(),
            user=mock_user,
        )
        async for _ in gen():
            pass

    kwargs = stream.calls[0]
    assert kwargs["venice_parameters"] == {"disable_thinking": True}
    assert "reasoning" not in kwargs


def _stream_generator(mock_user):
    return chat_streaming.build_stream_generator(
        logger=MagicMock(),
        session_id="0xold",
        body=BODY,
        requested_model="llama-3.3-70b",
        model_id="0x01",
        db_api_key=MagicMock(),
        user=mock_user,
    )


async def test_streaming_translates_session_renewal_retry(mock_user):
    outcomes = [SESSION_EXPIRED, FakeStreamResponse(GOOD_CHUNKS)]
    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      _stream_cm_factory(outcomes)) as stream, \
         patch.object(chat_streaming.chat_failover, "attempt_failover",
                      new_callable=AsyncMock) as failover, \
         patch.object(chat_streaming.session_routing_service, "invalidate_session",
                      new_callable=AsyncMock, return_value=True), \
         patch.object(chat_streaming.session_routing_service, "route_request",
                      new_callable=AsyncMock, return_value="0xnew"), \
         patch.object(chat_streaming.session_routing_service, "release_session",
                      new_callable=AsyncMock), \
         patch.object(chat_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock), \
         patch.object(chat_streaming, "translate_for_session",
                      _spec_by_session({"0xold": VENICE, "0xnew": VLLM})), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        async for _ in _stream_generator(mock_user)():
            pass

    failover.assert_not_awaited()
    first, second = stream.calls[0], stream.calls[1]
    assert first["session_id"] == "0xold" and first["venice_parameters"] == {"disable_thinking": True}
    assert second["session_id"] == "0xnew" and second["chat_template_kwargs"] == {"enable_thinking": False}
    assert "venice_parameters" not in second and "reasoning" not in second


async def test_wire_params_skips_deepcopy_when_no_canonical_fields():
    chat_params = {"temperature": 0.7}
    with patch.object(chat_non_streaming, "translate_for_session", new_callable=AsyncMock) as tfs:
        result = await chat_non_streaming.wire_params(chat_params, "0xsess", "0x01")
    assert result is chat_params
    tfs.assert_not_awaited()

    with patch.object(chat_streaming, "translate_for_session", new_callable=AsyncMock) as tfs2:
        result2 = await chat_streaming.wire_params(chat_params, "0xsess", "0x01")
    assert result2 is chat_params
    tfs2.assert_not_awaited()


async def test_wire_params_deepcopies_when_canonical_field_present():
    chat_params = {"reasoning": {"enabled": False}}
    with patch.object(chat_non_streaming, "translate_for_session", new_callable=AsyncMock) as tfs:
        result = await chat_non_streaming.wire_params(chat_params, "0xsess", "0x01")
    assert result is not chat_params
    tfs.assert_awaited_once()

    with patch.object(chat_streaming, "translate_for_session", new_callable=AsyncMock) as tfs2:
        result2 = await chat_streaming.wire_params(chat_params, "0xsess", "0x01")
    assert result2 is not chat_params
    tfs2.assert_awaited_once()


async def test_streaming_translates_failover_retry(mock_user):
    outcomes = [PROVIDER_DOWN, FakeStreamResponse(GOOD_CHUNKS)]
    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      _stream_cm_factory(outcomes)) as stream, \
         patch.object(chat_streaming.chat_failover, "attempt_failover",
                      new_callable=AsyncMock, return_value="0xnew"), \
         patch.object(chat_streaming.session_routing_service, "release_session",
                      new_callable=AsyncMock), \
         patch.object(chat_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock), \
         patch.object(chat_streaming, "translate_for_session",
                      _spec_by_session({"0xold": VENICE, "0xnew": VLLM})):
        async for _ in _stream_generator(mock_user)():
            pass

    first, second = stream.calls[0], stream.calls[1]
    assert first["session_id"] == "0xold" and first["venice_parameters"] == {"disable_thinking": True}
    assert second["session_id"] == "0xnew" and second["chat_template_kwargs"] == {"enable_thinking": False}
    assert "venice_parameters" not in second and "reasoning" not in second
