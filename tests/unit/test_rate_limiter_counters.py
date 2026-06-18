"""Unit tests for H4: fixed-window counter rate limiter.

Verify the Python wiring of the counter-based limiter — window-stamped keys,
the O(1) command set (evalsha INCRBY check, GET usage, INCRBY/EXPIRE record,
SCAN reset), and fail-open behavior. The Lua counting semantics themselves are
exercised against real Redis separately.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services.rate_limiting.redis_limiter import RedisRateLimiter
from src.services.rate_limiting.types import RateLimitConfig


def _cfg(rpm=60, tpm=1000, window=60):
    return RateLimitConfig(rpm=rpm, tpm=tpm, window_seconds=window)


def _limiter_with(fake_redis):
    lim = RedisRateLimiter()
    lim._initialized = True
    lim._redis = fake_redis
    lim._script_sha = "deadbeef"
    return lim


async def _aiter(items):
    for i in items:
        yield i


# --------------------------------------------------------------------------- #
# Key format
# --------------------------------------------------------------------------- #


def test_key_is_window_stamped():
    lim = RedisRateLimiter()
    assert lim._get_key("7", "rpm", None, 1700) == "ratelimit:rpm:7:1700"
    assert lim._get_key("7", "tpm", "g1", 1700) == "ratelimit:tpm:g1:7:1700"
    # prefix (no window) used for SCAN
    assert lim._get_key("7", "rpm", None) == "ratelimit:rpm:7"
    assert lim._get_key("7", "rpm", "g1") == "ratelimit:rpm:g1:7"


def test_window_start_is_clock_aligned():
    lim = RedisRateLimiter()
    ws = lim._window_start(_cfg(window=60))
    assert ws % 60 == 0


# --------------------------------------------------------------------------- #
# RPM check (evalsha O(1) counter)
# --------------------------------------------------------------------------- #


async def test_rpm_check_calls_evalsha_with_counter_args():
    fake = MagicMock()
    fake.evalsha = AsyncMock(return_value=[3, 1, 70])
    lim = _limiter_with(fake)

    count, limit, allowed = await lim.check_and_increment_rpm("u1", _cfg(rpm=60))

    assert (count, limit, allowed) == (3, 60, True)
    args = fake.evalsha.await_args.args
    assert args[0] == "deadbeef"          # script sha
    assert args[1] == 1                    # numkeys
    assert args[2].startswith("ratelimit:rpm:u1:")  # window-stamped key
    assert args[3] == "60"                 # limit
    assert args[4] == "1"                  # increment
    assert args[5] == "70"                 # ttl = window + 10


async def test_rpm_check_blocked_when_script_denies():
    fake = MagicMock()
    fake.evalsha = AsyncMock(return_value=[60, 0, 70])
    lim = _limiter_with(fake)
    count, limit, allowed = await lim.check_and_increment_rpm("u1", _cfg(rpm=60))
    assert allowed is False
    assert count == 60


async def test_rpm_check_fails_open_on_redis_error():
    fake = MagicMock()
    fake.evalsha = AsyncMock(side_effect=RuntimeError("redis down"))
    lim = _limiter_with(fake)
    count, limit, allowed = await lim.check_and_increment_rpm("u1", _cfg(rpm=60))
    assert (count, limit, allowed) == (0, 60, True)  # fail open


# --------------------------------------------------------------------------- #
# TPM record (INCRBY token_count + EXPIRE)
# --------------------------------------------------------------------------- #


async def test_add_tokens_incrby_and_expire():
    pipe = MagicMock()
    pipe.incrby = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[123, True])
    fake = MagicMock()
    fake.pipeline = MagicMock(return_value=pipe)
    lim = _limiter_with(fake)

    ok = await lim.add_tokens("u1", 123, _cfg(window=60))

    assert ok is True
    key = pipe.incrby.call_args.args[0]
    assert key.startswith("ratelimit:tpm:u1:")
    assert pipe.incrby.call_args.args[1] == 123     # INCRBY by token count
    assert pipe.expire.call_args.args[1] == 70      # ttl
    pipe.execute.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Usage read (two GETs)
# --------------------------------------------------------------------------- #


async def test_get_current_usage_uses_two_gets():
    pipe = MagicMock()
    pipe.get = MagicMock()
    pipe.execute = AsyncMock(return_value=["5", "900"])
    fake = MagicMock()
    fake.pipeline = MagicMock(return_value=pipe)
    lim = _limiter_with(fake)

    rpm, tpm = await lim.get_current_usage("u1", _cfg())

    assert (rpm, tpm) == (5, 900)
    assert pipe.get.call_count == 2
    assert pipe.get.call_args_list[0].args[0].startswith("ratelimit:rpm:u1:")
    assert pipe.get.call_args_list[1].args[0].startswith("ratelimit:tpm:u1:")


async def test_get_current_usage_missing_keys_are_zero():
    pipe = MagicMock()
    pipe.get = MagicMock()
    pipe.execute = AsyncMock(return_value=[None, None])
    fake = MagicMock()
    fake.pipeline = MagicMock(return_value=pipe)
    lim = _limiter_with(fake)
    assert await lim.get_current_usage("u1", _cfg()) == (0, 0)


# --------------------------------------------------------------------------- #
# Reset (SCAN window-stamped prefixes)
# --------------------------------------------------------------------------- #


async def test_reset_scans_and_deletes_user_keys():
    fake = MagicMock()
    fake.delete = AsyncMock()
    seen_patterns = []

    def scan_iter(match=None):
        seen_patterns.append(match)
        if match.startswith("ratelimit:rpm:"):
            return _aiter(["ratelimit:rpm:u1:1700", "ratelimit:rpm:u1:1760"])
        return _aiter(["ratelimit:tpm:u1:1700"])

    fake.scan_iter = scan_iter
    lim = _limiter_with(fake)

    ok = await lim.reset_user_limits("u1")

    assert ok is True
    assert seen_patterns == ["ratelimit:rpm:u1:*", "ratelimit:tpm:u1:*"]
    assert fake.delete.await_count == 3  # 2 rpm windows + 1 tpm window
