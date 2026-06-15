"""Lightweight time-based circuit breaker for optional external dependencies.

Wraps Redis (cache + rate limiting) so a *timeout-mode* outage (blackholed IP,
hung failover) does not make every request pay repeated multi-second connect
timeouts. On failure the breaker "opens" for an exponentially backed-off
cooldown during which callers skip the dependency and degrade immediately
(`is_open()` → True). A single background re-probe (`run_reprobe`) owns
recovery, so connection attempts stay off the request path entirely.

The breaker itself is pure and synchronous (trivially unit-testable); the async
recovery loop is a free function so each service can supply its own probe.
"""
import asyncio
import time
from typing import Awaitable, Callable


class CircuitBreaker:
    """Pure open/closed state with exponential-backoff cooldown.

    Closed (`is_open()` False): callers may use the dependency.
    Open  (`is_open()` True):   callers must skip it and degrade.
    `record_failure()` opens (and lengthens) the cooldown; `record_success()`
    closes it and resets the backoff.
    """

    def __init__(self, name: str, initial_backoff: float = 1.0, max_backoff: float = 30.0):
        self.name = name
        self._initial = initial_backoff
        self._max = max_backoff
        self._failures = 0
        self._open_until = 0.0  # monotonic deadline

    def is_open(self) -> bool:
        return time.monotonic() < self._open_until

    @property
    def cooldown_remaining(self) -> float:
        return max(0.0, self._open_until - time.monotonic())

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    def record_success(self) -> None:
        self._failures = 0
        self._open_until = 0.0

    def record_failure(self) -> float:
        """Open the breaker for the next backoff window. Returns the cooldown (s)."""
        self._failures += 1
        backoff = min(self._initial * (2 ** (self._failures - 1)), self._max)
        self._open_until = time.monotonic() + backoff
        return backoff


async def run_reprobe(
    breaker: CircuitBreaker,
    probe: Callable[[], Awaitable[bool]],
) -> None:
    """Background recovery loop: wait out the cooldown, probe once, repeat until
    the probe succeeds — keeping reconnection attempts off the request path.

    `probe` must itself update the breaker (success closes it, failure re-opens
    with a longer cooldown); this loop only paces the attempts. Designed to be
    run as a task and cancelled on shutdown.
    """
    while breaker.is_open():
        await asyncio.sleep(max(breaker.cooldown_remaining, 0.05))
        if breaker.is_open():
            # Re-opened (or not yet due) — keep waiting rather than probing early.
            continue
        if await probe():
            return
