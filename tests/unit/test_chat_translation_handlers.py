"""Handlers translate canonical reasoning params per attempt (initial + failover).

Covers:
- both parsers drop a client-supplied `request_id` (it collides with the
  handlers' own `request_id=` keyword)
- the non-streaming handler translates the initial attempt AND the failover
  retry from the canonical params (not from the previous attempt's wire params)
- the streaming handler translates the initial attempt from the canonical params
"""
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


def _spec_by_session(mapping):
    async def fake(body, session_id, model_id):
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


# --- streaming fakes (copied from tests/unit/test_chat_streaming_failover.py) ---

class FakeStreamResponse:
    """Mimics httpx streaming response."""

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
    """Like the failover tests' factory, but also records each call's kwargs
    so translation can be asserted on."""
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
