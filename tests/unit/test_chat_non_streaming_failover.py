"""Integration tests for non-streaming recovery: provider failover + expired-session renewal."""
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.api.v1.chat import chat_non_streaming
from src.db.models import SessionState
from src.services.proxy_router_service import ProxyRouterServiceError

BODY = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()


def _success_response():
    return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})


PROVIDER_DOWN = ProxyRouterServiceError(
    'HTTP 500: {"error":"provider request failed: failed to connect to provider: connection refused"}',
    status_code=500,
    error_type="server_error",
)
SESSION_EXPIRED = ProxyRouterServiceError(
    'HTTP 500: {"error":"session expired"}', status_code=500, error_type="server_error",
)
USER_ERROR = ProxyRouterServiceError(
    'HTTP 400: {"error":"invalid request"}', status_code=400, error_type="client_error",
)


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


def _call(mock_user, **overrides):
    kwargs = dict(
        logger=MagicMock(),
        request_id="req-1",
        body=BODY,
        db_api_key=MagicMock(),
        user=mock_user,
        requested_model="llama-3.3-70b",
        model_id="0xmodel",
        session_id="0xdead",
    )
    kwargs.update(overrides)
    return chat_non_streaming.handle_non_streaming_request(**kwargs)


async def test_provider_down_triggers_single_failover_retry(mock_user):
    with patch.object(chat_non_streaming.proxy_router_service, "chatCompletions",
                      new_callable=AsyncMock, side_effect=[PROVIDER_DOWN, _success_response()]) as chat, \
         patch.object(chat_non_streaming.chat_failover, "attempt_failover",
                      new_callable=AsyncMock, return_value="0xnew") as failover, \
         patch.object(chat_non_streaming.session_routing_service, "release_session",
                      new_callable=AsyncMock) as release, \
         patch.object(chat_non_streaming, "get_db", _fake_get_db()):
        response = await _call(mock_user)

    assert response.status_code == 200
    assert chat.await_count == 2
    assert chat.await_args_list[1].kwargs["session_id"] == "0xnew"
    failover.assert_awaited_once()
    assert failover.await_args.kwargs["original_session_id"] == "0xdead"
    # New session released exactly once; original is NOT released here
    # (index.py's finally owns it).
    release.assert_awaited_once()
    assert release.await_args.args[1] == "0xnew"


async def test_session_expired_triggers_renewal_not_failover(mock_user):
    """Expired session -> existing renewal mechanism (now reachable), not failover."""
    with patch.object(chat_non_streaming.proxy_router_service, "chatCompletions",
                      new_callable=AsyncMock, side_effect=[SESSION_EXPIRED, _success_response()]) as chat, \
         patch.object(chat_non_streaming.chat_failover, "attempt_failover",
                      new_callable=AsyncMock) as failover, \
         patch.object(chat_non_streaming.session_routing_service, "invalidate_session",
                      new_callable=AsyncMock, return_value=True) as invalidate, \
         patch.object(chat_non_streaming.session_routing_service, "route_request",
                      new_callable=AsyncMock, return_value="0xrenewed") as route, \
         patch.object(chat_non_streaming.session_routing_service, "release_session",
                      new_callable=AsyncMock) as release, \
         patch.object(chat_non_streaming, "get_db", _fake_get_db()), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        response = await _call(mock_user)

    assert response.status_code == 200
    failover.assert_not_awaited()
    assert chat.await_count == 2
    assert chat.await_args_list[1].kwargs["session_id"] == "0xrenewed"
    # Old row marked EXPIRED (not just released) so route_request can't re-pick it.
    invalidate.assert_awaited_once()
    assert invalidate.await_args.kwargs["state"] is SessionState.EXPIRED
    route.assert_awaited_once()
    # Renewal session released exactly once; original NOT released here.
    release.assert_awaited_once()
    assert release.await_args.args[1] == "0xrenewed"


async def test_user_error_is_not_retried(mock_user):
    with patch.object(chat_non_streaming.proxy_router_service, "chatCompletions",
                      new_callable=AsyncMock, side_effect=[USER_ERROR]) as chat, \
         patch.object(chat_non_streaming.chat_failover, "attempt_failover",
                      new_callable=AsyncMock) as failover:
        response = await _call(mock_user)

    assert response.status_code == 400
    assert chat.await_count == 1
    failover.assert_not_awaited()


async def test_no_alternate_session_surfaces_original_error(mock_user):
    with patch.object(chat_non_streaming.proxy_router_service, "chatCompletions",
                      new_callable=AsyncMock, side_effect=[PROVIDER_DOWN]), \
         patch.object(chat_non_streaming.chat_failover, "attempt_failover",
                      new_callable=AsyncMock, return_value=None):
        response = await _call(mock_user)

    assert response.status_code == 500
    payload = json.loads(response.body)
    assert payload["error"]["type"] == "server_error"


async def test_failed_retry_returns_real_status_not_200(mock_user):
    retry_error = ProxyRouterServiceError(
        'HTTP 500: {"error":"provider request failed"}', status_code=500,
        error_type="server_error",
    )
    with patch.object(chat_non_streaming.proxy_router_service, "chatCompletions",
                      new_callable=AsyncMock, side_effect=[PROVIDER_DOWN, retry_error]), \
         patch.object(chat_non_streaming.chat_failover, "attempt_failover",
                      new_callable=AsyncMock, return_value="0xnew"), \
         patch.object(chat_non_streaming.session_routing_service, "release_session",
                      new_callable=AsyncMock), \
         patch.object(chat_non_streaming, "get_db", _fake_get_db()):
        response = await _call(mock_user)

    # Real failure status so index.py voids (not finalizes) the billing hold.
    assert response.status_code == 500
