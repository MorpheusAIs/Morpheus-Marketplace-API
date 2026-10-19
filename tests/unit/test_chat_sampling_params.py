"""temperature and top_p reach the proxy-router only when the client sent them.

An injected 1.0 overrides the model's own sampling defaults, and kimi-k3,
which fixes top_p at 0.95, rejects it ("Parameter 'top_p'=1.0 is not supported").
"""
import json
import os
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.api.v1.chat import chat_non_streaming, chat_streaming  # noqa: E402
from src.api.v1.chat import index as chat_index  # noqa: E402
from src.api.v1.chat.chat_models import ChatCompletionRequest  # noqa: E402
from src.schemas import openai as openai_schemas  # noqa: E402
from src.services.proxy_router_service import ProxyRouterServiceError  # noqa: E402

SAMPLING = ("temperature", "top_p")
MESSAGES = [{"role": "user", "content": "hi"}]
# 0 and 1.0 are the values a falsy check or a default comparison would lose.
EXPLICIT = [
    {"temperature": 0, "top_p": 0},
    {"temperature": 1.0, "top_p": 1.0},
    {"temperature": 0.7, "top_p": 0.9},
    {"temperature": 0.2},
]
CASES = [{}] + EXPLICIT

PROVIDER_DOWN = ProxyRouterServiceError(
    'HTTP 500: {"error":"provider request failed: failed to connect to provider: connection refused"}',
    status_code=500,
    error_type="server_error",
)


def _sampling(params: dict) -> dict:
    return {k: params[k] for k in SAMPLING if k in params}


def _fake_get_db():
    db = AsyncMock()

    class FakeGetDb:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *args):
            return False

    return lambda: FakeGetDb()


@pytest.mark.parametrize("schema", [ChatCompletionRequest, openai_schemas.ChatCompletionRequest])
@pytest.mark.parametrize("sent", CASES)
def test_schema_dump_carries_only_client_sent_sampling_params(schema, sent):
    dumped = schema.model_validate({"model": "kimi-k3", "messages": MESSAGES, **sent}).model_dump(exclude_none=True)
    assert _sampling(dumped) == sent


def test_openapi_documents_sampling_params_as_optional_without_a_default():
    from src import main as main_module

    spec = main_module.app.openapi()
    props = spec["components"]["schemas"]["ChatCompletionRequest"]["properties"]
    for name in SAMPLING:
        assert props[name].get("default") is None
        assert "provider's default" in props[name]["description"]

    # Without a body example, swagger-ui samples the nullable fields as 0 and
    # "Try it out" would send temperature=0, top_p=0.
    examples = spec["paths"]["/api/v1/chat/completions"]["post"]["requestBody"]["content"]["application/json"]["examples"]
    assert examples
    for example in examples.values():
        ChatCompletionRequest.model_validate(example["value"])
        assert _sampling(example["value"]) == {}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("sent", CASES)
async def test_route_forwards_only_client_sent_sampling_params(stream, sent):
    request_data = ChatCompletionRequest.model_validate(
        {"model": "kimi-k3", "messages": MESSAGES, "stream": stream, **sent}
    )
    with patch.object(chat_index, "_check_rate_limits", new_callable=AsyncMock, return_value=MagicMock()), \
         patch.object(chat_index, "_create_billing_hold", new_callable=AsyncMock,
                      return_value=(uuid.uuid4(), "0x01", MagicMock(), "kimi-k3")), \
         patch.object(chat_index, "_resolve_session", new_callable=AsyncMock, return_value="0xsess"), \
         patch.object(chat_index, "_prepare_translation_headers", new_callable=AsyncMock, return_value={}), \
         patch.object(chat_index, "_handle_non_streaming_request", new_callable=AsyncMock) as non_streaming, \
         patch.object(chat_index, "_handle_streaming_request") as streaming:
        await chat_index.create_chat_completion(
            request_data, MagicMock(headers={}), user=MagicMock(id=1), db_api_key=MagicMock(id=7, key_prefix="abc")
        )

    handler = streaming if stream else non_streaming
    other = non_streaming if stream else streaming
    assert handler.call_count == 1 and other.call_count == 0
    body = json.loads(handler.call_args.kwargs["body"])
    assert _sampling(body) == sent
    assert body["stream"] is stream


@pytest.mark.parametrize("sent", CASES)
async def test_non_streaming_wire_payload_on_first_attempt_and_failover_retry(sent):
    body = json.dumps({"messages": MESSAGES, "stream": False, **sent}).encode()
    success = httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    with patch.object(chat_non_streaming.proxy_router_service, "_execute_request",
                      new_callable=AsyncMock, side_effect=[PROVIDER_DOWN, success]) as execute, \
         patch.object(chat_non_streaming.chat_failover, "attempt_failover",
                      new_callable=AsyncMock, return_value="0xnew"), \
         patch.object(chat_non_streaming.session_routing_service, "release_session", new_callable=AsyncMock), \
         patch.object(chat_non_streaming, "get_db", _fake_get_db()):
        response = await chat_non_streaming.handle_non_streaming_request(
            logger=MagicMock(),
            request_id="req-1",
            body=body,
            db_api_key=MagicMock(),
            user=MagicMock(id=42),
            requested_model="kimi-k3",
            model_id="0xmodel",
            session_id="0xdead",
        )

    assert response.status_code == 200
    assert execute.await_count == 2
    sessions = [call.kwargs["headers"]["session_id"] for call in execute.await_args_list]
    assert sessions == ["0xdead", "0xnew"]
    for call in execute.await_args_list:
        assert _sampling(call.kwargs["json_data"]) == sent


class _FakeStreamResponse:
    status_code = 200
    headers = {}

    async def aiter_bytes(self):
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        yield b"data: [DONE]\n\n"


class _FakeStreamCM:
    def __init__(self, outcome):
        self._outcome = outcome

    async def __aenter__(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome

    async def __aexit__(self, *args):
        return False


@pytest.mark.parametrize("sent", CASES)
async def test_streaming_params_on_first_attempt_and_failover_retry(sent):
    body = json.dumps({"messages": MESSAGES, "stream": True, **sent}).encode()
    outcomes = iter([PROVIDER_DOWN, _FakeStreamResponse()])
    calls = []

    def stream(**kwargs):
        calls.append(kwargs)
        return _FakeStreamCM(next(outcomes))

    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream", side_effect=stream), \
         patch.object(chat_streaming.chat_failover, "attempt_failover", new_callable=AsyncMock, return_value="0xnew"), \
         patch.object(chat_streaming.session_routing_service, "release_session", new_callable=AsyncMock), \
         patch.object(chat_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock):
        generator = chat_streaming.build_stream_generator(
            logger=MagicMock(),
            session_id="0xdead",
            body=body,
            requested_model="kimi-k3",
            model_id="0xmodel",
            db_api_key=MagicMock(),
            user=MagicMock(id=42),
        )()
        chunks = [c async for c in generator]

    assert chunks[-1] == b"data: [DONE]\n\n"
    assert [call["session_id"] for call in calls] == ["0xdead", "0xnew"]
    for call in calls:
        assert _sampling(call) == sent
