"""Tests for streaming recovery: provider failover + expired-session renewal."""
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.api.v1.chat import chat_streaming
from src.db.models import SessionState
from src.services.proxy_router_service import ProxyRouterServiceError

BODY = json.dumps({"messages": [{"role": "user", "content": "hi"}], "stream": True}).encode()

PROVIDER_DOWN = ProxyRouterServiceError(
    'HTTP 500: {"error":"provider request failed: failed to connect to provider"}',
    status_code=500,
    error_type="server_error",
)
SESSION_EXPIRED = ProxyRouterServiceError(
    'HTTP 500: {"error":"session expired"}', status_code=500, error_type="server_error",
)


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
    it = iter(outcomes)

    def factory(**kwargs):
        return FakeStreamCM(next(it))

    return factory


def _fake_get_db():
    db = AsyncMock()

    class FakeGetDb:
        async def __aenter__(self):
            return db
        async def __aexit__(self, *args):
            return False

    return lambda: FakeGetDb()


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = 42
    return user


async def _collect(gen):
    return [c async for c in gen]


def _generator(mock_user):
    return chat_streaming.build_stream_generator(
        logger=MagicMock(),
        session_id="0xdead",
        body=BODY,
        requested_model="llama-3.3-70b",
        model_id="0xmodel",
        db_api_key=MagicMock(),
        user=mock_user,
    )()


GOOD_CHUNKS = [b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', b"data: [DONE]\n\n"]


async def test_pre_first_token_provider_failure_fails_over_transparently(mock_user):
    outcomes = [PROVIDER_DOWN, FakeStreamResponse(GOOD_CHUNKS)]
    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      side_effect=_stream_cm_factory(outcomes)), \
         patch.object(chat_streaming.chat_failover, "attempt_failover",
                      new_callable=AsyncMock, return_value="0xnew") as failover, \
         patch.object(chat_streaming.session_routing_service, "release_session",
                      new_callable=AsyncMock), \
         patch.object(chat_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock):
        chunks = await _collect(_generator(mock_user))

    failover.assert_awaited_once()
    # Client must see ONLY the successful stream — no error chunk.
    assert chunks == GOOD_CHUNKS


async def test_pre_first_token_session_expiry_renews_not_fails_over(mock_user):
    """Expired session -> renewal mechanism (now reachable), not failover."""
    outcomes = [SESSION_EXPIRED, FakeStreamResponse(GOOD_CHUNKS)]
    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      side_effect=_stream_cm_factory(outcomes)), \
         patch.object(chat_streaming.chat_failover, "attempt_failover",
                      new_callable=AsyncMock) as failover, \
         patch.object(chat_streaming.session_routing_service, "invalidate_session",
                      new_callable=AsyncMock, return_value=True) as invalidate, \
         patch.object(chat_streaming.session_routing_service, "route_request",
                      new_callable=AsyncMock, return_value="0xrenewed"), \
         patch.object(chat_streaming.session_routing_service, "release_session",
                      new_callable=AsyncMock) as release, \
         patch.object(chat_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        chunks = await _collect(_generator(mock_user))

    failover.assert_not_awaited()
    invalidate.assert_awaited_once()
    assert invalidate.await_args.kwargs["state"] is SessionState.EXPIRED
    assert chunks == GOOD_CHUNKS
    # Renewal session released exactly once (by _handle_session_retry's finally).
    release.assert_awaited_once()
    assert release.await_args.args[1] == "0xrenewed"


async def test_mid_stream_failure_is_not_retried(mock_user):
    class ExplodingResponse(FakeStreamResponse):
        async def aiter_bytes(self):
            yield GOOD_CHUNKS[0]
            raise PROVIDER_DOWN

    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      side_effect=_stream_cm_factory([ExplodingResponse([])])), \
         patch.object(chat_streaming.chat_failover, "attempt_failover",
                      new_callable=AsyncMock) as failover, \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock):
        chunks = await _collect(_generator(mock_user))

    failover.assert_not_awaited()
    assert chunks[0] == GOOD_CHUNKS[0]
    assert b"error" in chunks[-1]


async def test_failover_impossible_emits_error_chunk(mock_user):
    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      side_effect=_stream_cm_factory([PROVIDER_DOWN])), \
         patch.object(chat_streaming.chat_failover, "attempt_failover",
                      new_callable=AsyncMock, return_value=None), \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock):
        chunks = await _collect(_generator(mock_user))

    assert len(chunks) == 1
    assert b"error" in chunks[0]


async def test_renewal_route_failure_emits_error_chunk(mock_user):
    """When renewal can't route a new session, the client must still get an
    error chunk (previously the stream would just end empty)."""
    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      side_effect=_stream_cm_factory([SESSION_EXPIRED])), \
         patch.object(chat_streaming.session_routing_service, "invalidate_session",
                      new_callable=AsyncMock, return_value=True), \
         patch.object(chat_streaming.session_routing_service, "route_request",
                      new_callable=AsyncMock, side_effect=Exception("no session")), \
         patch.object(chat_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        chunks = await _collect(_generator(mock_user))

    assert any(b"error" in c for c in chunks)


async def test_failover_retry_failure_emits_error_chunk(mock_user):
    with patch.object(chat_streaming.proxy_router_service, "chatCompletionsStream",
                      side_effect=_stream_cm_factory([PROVIDER_DOWN, PROVIDER_DOWN])), \
         patch.object(chat_streaming.chat_failover, "attempt_failover",
                      new_callable=AsyncMock, return_value="0xnew"), \
         patch.object(chat_streaming.session_routing_service, "release_session",
                      new_callable=AsyncMock) as release, \
         patch.object(chat_streaming, "get_db", _fake_get_db()), \
         patch.object(chat_streaming, "_stream_cleanup", new_callable=AsyncMock):
        chunks = await _collect(_generator(mock_user))

    assert any(b"error" in c for c in chunks)
    # New session released exactly once by the failover retry path.
    release.assert_awaited_once()
