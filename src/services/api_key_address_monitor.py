"""
API key address monitor

Counts the distinct caller addresses that use each API key in a UTC day and
logs one ``api_key_many_addresses`` event the first time a key crosses
``API_KEY_ADDRESS_ALERT_THRESHOLD``. A key that is suddenly used from many
addresses is what a shared or leaked key looks like.

It only reports. It never blocks a request or revokes a key, and a Redis
outage turns it off rather than slowing or failing the request: the work runs
in a background task and every failure is swallowed.

Counting uses one Redis HyperLogLog per key per day, so memory stays small
however many addresses a key is used from. A marker key makes the event fire
once per key per day across all workers.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional, Set, Tuple

from src.core.config import settings
from src.core.logging_config import get_auth_logger
from src.services.rate_limiting.redis_limiter import RedisRateLimiter, redis_limiter

logger = get_auth_logger()

# Keys outlive their UTC day so a count is still readable the day after.
KEY_TTL_SECONDS = 2 * 24 * 60 * 60

# Upper bound on the per-process memo of (key, address) pairs already counted.
MAX_SEEN_ENTRIES = 50_000


class ApiKeyAddressMonitor:
    """Per-process front end to the per-day distinct-address count in Redis."""

    def __init__(self, limiter: RedisRateLimiter = redis_limiter) -> None:
        self._limiter = limiter
        self._day = ""
        # (api_key_id, client_ip) pairs this process has already counted today.
        # A client that keeps one address costs one Redis call per day.
        self._seen: Set[Tuple[int, str]] = set()
        # Keys already reported today; nothing more to learn by counting them.
        self._alerted: Set[int] = set()
        self._tasks: Set[asyncio.Task] = set()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d")

    def _roll_day(self, day: str) -> None:
        if day != self._day:
            self._day = day
            self._seen.clear()
            self._alerted.clear()

    def note(
        self, api_key_id: Optional[int], user_id: Optional[int], client_ip: str
    ) -> None:
        """
        Record that ``api_key_id`` was used from ``client_ip``. Returns at once;
        the Redis work, if any is needed, runs in a background task.
        """
        threshold = settings.API_KEY_ADDRESS_ALERT_THRESHOLD
        if (
            threshold <= 0
            or api_key_id is None
            or not client_ip
            or client_ip == "unknown"
        ):
            return

        day = self._today()
        self._roll_day(day)
        if api_key_id in self._alerted or (api_key_id, client_ip) in self._seen:
            return

        try:
            task = asyncio.create_task(
                self._record(api_key_id, user_id, client_ip, day, threshold)
            )
        except RuntimeError:
            # No running event loop (not on a request path).
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _record(
        self,
        api_key_id: int,
        user_id: Optional[int],
        client_ip: str,
        day: str,
        threshold: int,
    ) -> None:
        try:
            count = await self._limiter.add_distinct(
                f"keyaddr:{api_key_id}:{day}", client_ip, KEY_TTL_SECONDS
            )
            if count is None:
                # Redis degraded. Leave the pair unmarked so it is retried.
                return

            if day == self._day:
                if len(self._seen) >= MAX_SEEN_ENTRIES:
                    self._seen.clear()
                self._seen.add((api_key_id, client_ip))

            if count < threshold:
                return

            first = await self._limiter.set_if_absent(
                f"keyaddr:alerted:{api_key_id}:{day}", KEY_TTL_SECONDS
            )
            if first is None:
                return
            if day == self._day:
                self._alerted.add(api_key_id)
            if first:
                logger.warning(
                    "API key used from many addresses today",
                    api_key_id=api_key_id,
                    user_id=user_id,
                    distinct_addresses=count,
                    threshold=threshold,
                    day=day,
                    event_type="api_key_many_addresses",
                )
        except Exception as e:
            logger.debug(
                "API key address monitor failed",
                api_key_id=api_key_id,
                error=str(e),
                event_type="api_key_address_monitor_error",
            )


# Singleton instance
api_key_address_monitor = ApiKeyAddressMonitor()
