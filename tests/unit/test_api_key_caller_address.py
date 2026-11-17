"""Caller address logging for API-key requests, and the many-addresses alert.

Covers:
- the address resolver ignores a forwarded header the client forged;
- an API-key request logs the key ID, address and user agent, never the key;
- a chat request's start event carries the same fields;
- a key used from N distinct addresses in a day raises exactly one event.
"""
import asyncio
import os
import sys
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src import dependencies as deps  # noqa: E402
from src.api.v1.chat import index as chat_index  # noqa: E402
from src.core.client_ip import (  # noqa: E402
    MAX_USER_AGENT_LENGTH,
    resolve_client_ip,
    resolve_user_agent,
)
from src.services import api_key_address_monitor as monitor_module  # noqa: E402
from src.services.api_key_address_monitor import ApiKeyAddressMonitor  # noqa: E402

PREFIX = "sk-abc123"
RAW = PREFIX + "." + "a" * 64
LOAD_BALANCER = "10.0.3.17"


def _request(
    peer=LOAD_BALANCER,
    forwarded_for=None,
    user_agent="agent/1.0",
    path="/api/v1/chat/completions",
):
    headers = [(b"authorization", f"Bearer {RAW}".encode())]
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    if user_agent is not None:
        headers.append((b"user-agent", user_agent.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
        "query_string": b"",
        "client": (peer, 40000) if peer else None,
    }
    return Request(scope)


def _cached():
    return {
        "id": 41,
        "user_id": 7,
        "key_prefix": PREFIX,
        "hashed_key": "hashed",
        "encrypted_key": "enc",
        "is_active": True,
        "last_used_at": None,
        "created_at": None,
        "name": "k",
        "encryption_version": 1,
        "is_default": False,
        "user": {
            "id": 7,
            "is_active": True,
            "cognito_user_id": "cog",
            "created_at": None,
            "updated_at": None,
            "rate_limit_multiplier": 1.0,
        },
    }


def _logged(mock_logger, event_type):
    """Keyword arguments of every call on the mock logger with this event type."""
    return [
        call.kwargs
        for call in mock_logger.mock_calls
        if call.kwargs.get("event_type") == event_type
    ]


# --------------------------------------------------------------------------- #
# Address resolver
# --------------------------------------------------------------------------- #


def test_address_behind_load_balancer_is_rightmost_hop():
    request = _request(forwarded_for="203.0.113.9")
    assert resolve_client_ip(request) == "203.0.113.9"


def test_forged_forwarded_header_does_not_change_address():
    # The client sends its own X-Forwarded-For; the load balancer appends the
    # real address on the right.
    request = _request(forwarded_for="1.2.3.4, 198.51.100.7")
    assert resolve_client_ip(request) == "198.51.100.7"


def test_forwarded_header_ignored_on_direct_connection():
    # The peer must be a routable address here: Python's is_private is also
    # true for the documentation ranges (198.51.100.0/24 and the like).
    request = _request(peer="93.184.216.34", forwarded_for="1.2.3.4")
    assert resolve_client_ip(request) == "93.184.216.34"


def test_address_unknown_without_peer():
    assert resolve_client_ip(_request(peer=None)) == "unknown"


def test_user_agent_is_truncated_and_optional():
    assert (
        resolve_user_agent(_request(user_agent="x" * 1000))
        == "x" * MAX_USER_AGENT_LENGTH
    )
    assert resolve_user_agent(_request(user_agent=None)) == ""


# --------------------------------------------------------------------------- #
# API key auth logs the caller
# --------------------------------------------------------------------------- #


async def _authenticate(request, auth_logger, note):
    """Run API key auth on a cache hit with a key that verifies."""
    with ExitStack() as stack:
        for target, replacement in (
            (
                "src.core.local_testing.is_local_testing_mode",
                MagicMock(return_value=False),
            ),
            ("src.dependencies.cache_service.get", AsyncMock(return_value=_cached())),
            ("src.dependencies.verify_api_key", MagicMock(return_value=True)),
            ("src.dependencies._update_api_key_last_used_background", AsyncMock()),
            ("src.dependencies.auth_logger", auth_logger),
            ("src.dependencies.api_key_address_monitor.note", note),
        ):
            stack.enter_context(patch(target, replacement))
        auth = await deps.get_api_key_auth(request=request, api_key_str=f"Bearer {RAW}")
        # Let the background last-used update finish inside the patches.
        await asyncio.sleep(0)
        return auth


async def test_api_key_request_logs_key_id_address_and_user_agent():
    auth_logger, note = MagicMock(), MagicMock()
    request = _request(
        forwarded_for="1.2.3.4, 198.51.100.7",
        user_agent="agent/1.0",
        path="/api/v1/embeddings",
    )

    auth = await _authenticate(request, auth_logger, note)

    assert auth.api_key.id == 41
    events = _logged(auth_logger, "api_key_request")
    assert len(events) == 1
    event = events[0]
    assert event["api_key_id"] == 41
    assert event["key_prefix"] == PREFIX
    assert event["user_id"] == 7
    assert event["client_ip"] == "198.51.100.7"
    assert event["user_agent"] == "agent/1.0"
    assert event["path"] == "/api/v1/embeddings"
    note.assert_called_once_with(41, 7, "198.51.100.7")


async def test_api_key_request_never_logs_the_key():
    auth_logger, note = MagicMock(), MagicMock()

    await _authenticate(_request(forwarded_for="198.51.100.7"), auth_logger, note)

    secret = RAW.split(".", 1)[1]
    assert auth_logger.mock_calls
    for call in auth_logger.mock_calls:
        assert secret not in repr(call)


async def test_rejected_key_is_not_recorded_as_use():
    auth_logger, note = MagicMock(), MagicMock()
    with patch(
        "src.core.local_testing.is_local_testing_mode", return_value=False
    ), patch(
        "src.dependencies.cache_service.get", AsyncMock(return_value=_cached())
    ), patch("src.dependencies.cache_service.delete", AsyncMock()), patch(
        "src.dependencies.verify_api_key", return_value=False
    ), patch("src.dependencies.auth_logger", auth_logger), patch(
        "src.dependencies.api_key_address_monitor.note", note
    ):
        with pytest.raises(HTTPException) as exc:
            await deps.get_api_key_auth(
                request=_request(forwarded_for="198.51.100.7"), api_key_str=RAW
            )

    assert exc.value.status_code == 401
    assert _logged(auth_logger, "api_key_request") == []
    note.assert_not_called()


async def test_logging_failure_does_not_fail_the_request():
    auth_logger = MagicMock()
    auth_logger.info.side_effect = RuntimeError("log pipeline down")

    auth = await _authenticate(
        _request(forwarded_for="198.51.100.7"), auth_logger, MagicMock()
    )

    assert auth.api_key.id == 41
    assert len(_logged(auth_logger, "api_key_request_log_failed")) == 1


# --------------------------------------------------------------------------- #
# Through FastAPI: both dependency paths receive the request
# --------------------------------------------------------------------------- #


def _app():
    app = FastAPI()

    @app.get("/key-only")
    async def key_only(api_key=Depends(deps.get_current_api_key)):
        return {"api_key_id": api_key.id}

    @app.get("/jwt-or-key")
    async def jwt_or_key(user=Depends(deps.get_user_jwt_or_api_key)):
        return {"user_id": user.id}

    app.dependency_overrides[deps.get_db_session] = lambda: MagicMock()
    return app


@pytest.mark.parametrize("path", ["/key-only", "/jwt-or-key"])
def test_request_reaches_the_auth_dependency_through_fastapi(path):
    auth_logger, note = MagicMock(), MagicMock()
    with ExitStack() as stack:
        for target, replacement in (
            (
                "src.core.local_testing.is_local_testing_mode",
                MagicMock(return_value=False),
            ),
            ("src.dependencies.cache_service.get", AsyncMock(return_value=_cached())),
            ("src.dependencies.verify_api_key", MagicMock(return_value=True)),
            ("src.dependencies._update_api_key_last_used_background", AsyncMock()),
            ("src.dependencies.auth_logger", auth_logger),
            ("src.dependencies.api_key_address_monitor.note", note),
        ):
            stack.enter_context(patch(target, replacement))
        response = TestClient(_app()).get(
            path,
            headers={"Authorization": f"Bearer {RAW}", "User-Agent": "agent/1.0"},
        )

    assert response.status_code == 200
    events = _logged(auth_logger, "api_key_request")
    assert len(events) == 1
    assert events[0]["api_key_id"] == 41
    assert events[0]["path"] == path
    assert events[0]["user_agent"] == "agent/1.0"
    # The test client's peer is the literal "testclient", not an address.
    assert events[0]["client_ip"] == "testclient"
    note.assert_called_once_with(41, 7, "testclient")


# --------------------------------------------------------------------------- #
# Chat start event
# --------------------------------------------------------------------------- #


class _StopAfterStartEvent(Exception):
    pass


async def test_chat_start_event_carries_key_id_address_and_user_agent():
    chat_logger = MagicMock()
    api_logger = MagicMock()
    api_logger.bind.return_value = chat_logger
    request = _request(forwarded_for="1.2.3.4, 198.51.100.7", user_agent="agent/1.0")
    request_data = MagicMock(model="some-model", stream=False)

    with patch.object(
        chat_index, "get_api_logger", return_value=api_logger
    ), patch.object(
        chat_index, "_check_rate_limits", AsyncMock(side_effect=_StopAfterStartEvent())
    ):
        with pytest.raises(_StopAfterStartEvent):
            await chat_index.create_chat_completion(
                request_data=request_data,
                request=request,
                user=MagicMock(id=7),
                db_api_key=MagicMock(id=41, key_prefix=PREFIX),
            )

    events = _logged(chat_logger, "chat_completion_request_start")
    assert len(events) == 1
    assert events[0]["api_key_id"] == 41
    assert events[0]["client_ip"] == "198.51.100.7"
    assert events[0]["user_agent"] == "agent/1.0"
    secret = RAW.split(".", 1)[1]
    for call in chat_logger.mock_calls:
        assert secret not in repr(call)


# --------------------------------------------------------------------------- #
# Many-addresses alert
# --------------------------------------------------------------------------- #


class FakeRedis:
    """In-memory stand-in for the two limiter calls the monitor uses."""

    def __init__(self):
        self.sets = {}
        self.markers = set()
        self.available = True
        self.add_calls = 0

    async def add_distinct(self, key, member, ttl_seconds):
        if not self.available:
            return None
        self.add_calls += 1
        self.sets.setdefault(key, set()).add(member)
        return len(self.sets[key])

    async def set_if_absent(self, key, ttl_seconds):
        if not self.available:
            return None
        if key in self.markers:
            return False
        self.markers.add(key)
        return True


@pytest.fixture
def alert_logger():
    with patch.object(monitor_module, "logger", MagicMock()) as mock_logger:
        yield mock_logger


@pytest.fixture
def threshold_20():
    with patch.object(monitor_module.settings, "API_KEY_ADDRESS_ALERT_THRESHOLD", 20):
        yield


async def _use(monitor, api_key_id, address, user_id=7):
    monitor.note(api_key_id, user_id, address)
    for task in list(monitor._tasks):
        await task


def _alerts(alert_logger):
    return _logged(alert_logger, "api_key_many_addresses")


async def test_twenty_addresses_in_a_day_raise_exactly_one_event(
    alert_logger, threshold_20
):
    monitor = ApiKeyAddressMonitor(limiter=FakeRedis())

    for i in range(19):
        await _use(monitor, 41, f"198.51.100.{i}")
    assert _alerts(alert_logger) == []

    await _use(monitor, 41, "198.51.100.19")
    alerts = _alerts(alert_logger)
    assert len(alerts) == 1
    assert alerts[0]["api_key_id"] == 41
    assert alerts[0]["user_id"] == 7
    assert alerts[0]["distinct_addresses"] == 20
    assert alerts[0]["threshold"] == 20

    for i in range(20, 60):
        await _use(monitor, 41, f"198.51.100.{i}")
    assert len(_alerts(alert_logger)) == 1


async def test_event_does_not_contain_addresses(alert_logger, threshold_20):
    monitor = ApiKeyAddressMonitor(limiter=FakeRedis())
    for i in range(20):
        await _use(monitor, 41, f"198.51.100.{i}")

    assert len(_alerts(alert_logger)) == 1
    assert "198.51.100." not in repr(alert_logger.mock_calls)


async def test_repeat_use_from_one_address_counts_once_and_skips_redis(
    alert_logger, threshold_20
):
    redis = FakeRedis()
    monitor = ApiKeyAddressMonitor(limiter=redis)

    for _ in range(500):
        await _use(monitor, 41, "198.51.100.1")

    assert redis.add_calls == 1
    assert _alerts(alert_logger) == []


async def test_keys_are_counted_separately(alert_logger, threshold_20):
    monitor = ApiKeyAddressMonitor(limiter=FakeRedis())

    for i in range(19):
        await _use(monitor, 41, f"198.51.100.{i}")
        await _use(monitor, 42, f"198.51.100.{i}")

    assert _alerts(alert_logger) == []


async def test_second_worker_does_not_repeat_the_event(alert_logger, threshold_20):
    redis = FakeRedis()
    worker_a = ApiKeyAddressMonitor(limiter=redis)
    worker_b = ApiKeyAddressMonitor(limiter=redis)

    for i in range(20):
        await _use(worker_a, 41, f"198.51.100.{i}")
    for i in range(20, 40):
        await _use(worker_b, 41, f"198.51.100.{i}")

    assert len(_alerts(alert_logger)) == 1


async def test_count_restarts_on_a_new_day(alert_logger, threshold_20):
    monitor = ApiKeyAddressMonitor(limiter=FakeRedis())

    with patch.object(ApiKeyAddressMonitor, "_today", return_value="20261117"):
        for i in range(20):
            await _use(monitor, 41, f"198.51.100.{i}")
    assert len(_alerts(alert_logger)) == 1

    with patch.object(ApiKeyAddressMonitor, "_today", return_value="20261118"):
        for i in range(19):
            await _use(monitor, 41, f"198.51.100.{i}")
        assert len(_alerts(alert_logger)) == 1
        await _use(monitor, 41, "198.51.100.19")
    assert len(_alerts(alert_logger)) == 2


async def test_redis_outage_is_silent_and_retried_later(alert_logger, threshold_20):
    redis = FakeRedis()
    monitor = ApiKeyAddressMonitor(limiter=redis)

    redis.available = False
    for i in range(20):
        await _use(monitor, 41, f"198.51.100.{i}")
    assert _alerts(alert_logger) == []

    redis.available = True
    for i in range(20):
        await _use(monitor, 41, f"198.51.100.{i}")
    assert len(_alerts(alert_logger)) == 1


async def test_monitor_never_raises(alert_logger, threshold_20):
    redis = FakeRedis()
    redis.add_distinct = AsyncMock(side_effect=RuntimeError("boom"))
    monitor = ApiKeyAddressMonitor(limiter=redis)

    await _use(monitor, 41, "198.51.100.1")

    assert _alerts(alert_logger) == []


async def test_threshold_zero_turns_the_monitor_off(alert_logger):
    redis = FakeRedis()
    monitor = ApiKeyAddressMonitor(limiter=redis)

    with patch.object(monitor_module.settings, "API_KEY_ADDRESS_ALERT_THRESHOLD", 0):
        for i in range(50):
            await _use(monitor, 41, f"198.51.100.{i}")

    assert redis.add_calls == 0
    assert _alerts(alert_logger) == []


async def test_unknown_address_is_not_counted(alert_logger, threshold_20):
    redis = FakeRedis()
    monitor = ApiKeyAddressMonitor(limiter=redis)

    await _use(monitor, 41, "unknown")
    await _use(monitor, 41, "")
    await _use(monitor, None, "198.51.100.1")

    assert redis.add_calls == 0
