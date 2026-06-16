"""Tests for the Redis circuit breaker and the degraded-mode behavior it gives
cache_service and redis_limiter (instant fail-open/degrade, no per-request dial).

No real Redis is used: the breaker is pure, and the service tests assert that an
open circuit short-circuits *before* any connection attempt, and that a
connectivity error trips the breaker. Background re-probe is patched out so tests
stay hermetic.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.core.circuit_breaker import CircuitBreaker, run_reprobe
from src.services.cache_service import CacheService
from src.services.rate_limiting.redis_limiter import RedisRateLimiter
from src.services.rate_limiting.types import RateLimitConfig


# --------------------------------------------------------------------------- #
# Pure CircuitBreaker
# --------------------------------------------------------------------------- #


def test_breaker_starts_closed():
    cb = CircuitBreaker("t")
    assert cb.is_open() is False
    assert cb.cooldown_remaining == 0.0


def test_failure_opens_with_backoff_then_success_closes():
    cb = CircuitBreaker("t", initial_backoff=5.0, max_backoff=30.0)
    backoff = cb.record_failure()
    assert backoff == 5.0
    assert cb.is_open() is True
    assert cb.cooldown_remaining > 0
    cb.record_success()
    assert cb.is_open() is False
    assert cb.consecutive_failures == 0


def test_backoff_is_exponential_and_capped():
    cb = CircuitBreaker("t", initial_backoff=1.0, max_backoff=8.0)
    assert cb.record_failure() == 1.0   # 1 * 2^0
    assert cb.record_failure() == 2.0   # 1 * 2^1
    assert cb.record_failure() == 4.0   # 1 * 2^2
    assert cb.record_failure() == 8.0   # 1 * 2^3
    assert cb.record_failure() == 8.0   # capped


async def test_run_reprobe_closes_breaker_on_successful_probe():
    cb = CircuitBreaker("t", initial_backoff=0.01, max_backoff=0.02)
    cb.record_failure()  # open it

    async def probe():
        cb.record_success()  # simulate a successful reconnect updating the breaker
        return True

    await run_reprobe(cb, probe)
    assert cb.is_open() is False


# --------------------------------------------------------------------------- #
# CacheService degraded-mode
# --------------------------------------------------------------------------- #


async def test_cache_open_circuit_returns_miss_without_dialing():
    svc = CacheService()
    svc._connect_locked = AsyncMock()  # must NOT be called
    svc._breaker.record_failure()      # open the circuit

    with patch("src.services.cache_service.settings.CACHE_ENABLED", True):
        result = await svc.get("api_key", "abc")

    assert result is None
    svc._connect_locked.assert_not_awaited()


async def test_cache_disabled_returns_miss_without_dialing():
    svc = CacheService()
    svc._connect_locked = AsyncMock()
    with patch("src.services.cache_service.settings.CACHE_ENABLED", False):
        assert await svc.get("api_key", "abc") is None
        assert await svc.set("api_key", "abc", {"x": 1}) is True
    svc._connect_locked.assert_not_awaited()


async def test_cache_connectivity_error_trips_breaker():
    svc = CacheService()
    svc._ensure_reprobe = MagicMock()  # don't spawn a real background task
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(side_effect=RedisConnectionError("redis down"))
    svc._acquire_redis = AsyncMock(return_value=fake_redis)

    with patch("src.services.cache_service.settings.CACHE_ENABLED", True):
        result = await svc.get("api_key", "abc")

    assert result is None                  # degrades gracefully
    assert svc._breaker.is_open() is True  # circuit tripped for next ops
    svc._ensure_reprobe.assert_called_once()


async def test_cache_non_connectivity_error_does_not_trip_breaker():
    svc = CacheService()
    svc._ensure_reprobe = MagicMock()
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(side_effect=ValueError("not a redis error"))
    svc._acquire_redis = AsyncMock(return_value=fake_redis)

    with patch("src.services.cache_service.settings.CACHE_ENABLED", True):
        result = await svc.get("api_key", "abc")

    assert result is None
    assert svc._breaker.is_open() is False
    svc._ensure_reprobe.assert_not_called()


async def test_cache_health_check_fast_when_circuit_open():
    svc = CacheService()
    svc._connect_locked = AsyncMock()  # must NOT dial
    svc._breaker.record_failure()
    with patch("src.services.cache_service.settings.CACHE_ENABLED", True):
        health = await svc.health_check()
    assert health["status"] == "degraded"
    assert health["circuit"] == "open"
    svc._connect_locked.assert_not_awaited()


# --------------------------------------------------------------------------- #
# RedisRateLimiter degraded-mode (fail open)
# --------------------------------------------------------------------------- #


def _cfg():
    return RateLimitConfig(rpm=100, tpm=10000, window_seconds=60)


async def test_limiter_open_circuit_fails_open_without_dialing():
    lim = RedisRateLimiter()
    lim._connect_locked = AsyncMock()  # must NOT be called
    lim._breaker.record_failure()      # open the circuit

    current, limit, allowed = await lim.check_and_increment_rpm("user1", _cfg())

    assert allowed is True   # fail open
    assert limit == 100
    lim._connect_locked.assert_not_awaited()


async def test_limiter_connectivity_error_trips_breaker_and_fails_open():
    lim = RedisRateLimiter()
    lim._ensure_reprobe = MagicMock()
    fake_redis = MagicMock()
    fake_redis.evalsha = AsyncMock(side_effect=RedisConnectionError("redis down"))
    lim._acquire_redis = AsyncMock(return_value=fake_redis)

    current, limit, allowed = await lim.check_and_increment_rpm("user1", _cfg())

    assert allowed is True                 # fail open
    assert lim._breaker.is_open() is True  # tripped for subsequent checks
    lim._ensure_reprobe.assert_called_once()


async def test_limiter_get_current_usage_degrades_to_zero():
    lim = RedisRateLimiter()
    lim._connect_locked = AsyncMock()
    lim._breaker.record_failure()
    assert await lim.get_current_usage("user1", _cfg()) == (0, 0)
    lim._connect_locked.assert_not_awaited()
