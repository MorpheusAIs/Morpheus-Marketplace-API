"""
Provider API spec lookup for request translation.

A provider's proxy-router reports, per model, an `api` block (serving stack,
model family, request-param bindings) in its morrpc pong. The gateway reaches
that report through its own consumer proxy-router:

    GET  blockchain/providers          -> provider address -> endpoint
    POST proxy/provider/ping           -> {"models": [{"modelId", "api", ...}]}

Results are cached per provider (the whole report) for
PROVIDER_API_SPEC_TTL_SECONDS; failures are negative-cached briefly so a dead
provider does not add a ping to every request.

Concurrency: the cache is read lock-free, so an already-cached lookup never
waits on anything. A miss/refresh for a given provider is serialized with a
per-provider `asyncio.Lock` (created on demand) so concurrent lookups for the
*same* provider ping it once, while lookups for unrelated providers never
wait on each other. The providers-list refresh (`getProviders()`) has its own
separate lock; a failure there backs off for `negative_ttl_seconds` instead
of being retried on every miss during an outage.
"""
import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

import structlog

from src.core.config import settings
from src.services import proxy_router_service

logger = structlog.get_logger(__name__)

# How long an otherwise-fresh endpoint list may go without a successful
# refresh before an address unknown to it forces one (a provider may simply
# be new on-chain since the last refresh).
UNKNOWN_PROVIDER_FORCE_REFRESH_SECONDS = 30


class ProviderApiSpecService:
    def __init__(
        self,
        ttl_seconds: Optional[int] = None,
        negative_ttl_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds if ttl_seconds is not None else settings.PROVIDER_API_SPEC_TTL_SECONDS
        self._negative_ttl = negative_ttl_seconds
        self._clock = clock
        # provider address (lowercase) -> (expires_at, {model_id(lowercase): api dict})
        self._reports: Dict[str, tuple] = {}
        # provider address (lowercase) -> endpoint; refreshed with the report TTL
        self._endpoints: Dict[str, str] = {}
        self._endpoints_expire_at: float = 0.0
        # Monotonic time of the last successful (not negative-cached) refresh,
        # or None if none has ever succeeded. Lets an unknown address force an
        # early refresh (a provider may have registered on-chain since the
        # last refresh) without fighting the negative-cache backoff on failure.
        self._endpoints_refreshed_at: Optional[float] = None
        # provider address (lowercase) -> the checksum-cased address as reported
        # by the proxy-router, kept per-instance so tests don't leak state.
        self._display: Dict[str, str] = {}
        # Per-provider locks (created on demand) so a miss/refresh for one
        # provider never blocks lookups for another. Dict access itself needs
        # no lock: asyncio is single-threaded and get_spec never awaits
        # between checking for a lock and creating one.
        self._provider_locks: Dict[str, asyncio.Lock] = {}
        # Guards refreshing the shared providers list, independent of any
        # per-provider lock.
        self._providers_lock = asyncio.Lock()

    def clear(self) -> None:
        self._reports.clear()
        self._endpoints.clear()
        self._endpoints_expire_at = 0.0
        self._endpoints_refreshed_at = None
        self._display.clear()
        self._provider_locks.clear()

    async def get_spec(self, provider_address: str, model_id: str) -> Optional[dict]:
        """Return the provider-declared `api` block for model_id, or None."""
        addr = (provider_address or "").strip().lower()
        mid = (model_id or "").strip().lower()
        if not addr or not mid:
            return None

        # Lock-free fast path: an already-fresh cache entry is returned
        # immediately, so a slow/dead provider elsewhere can never delay it.
        entry = self._reports.get(addr)
        if entry is not None and entry[0] > self._clock():
            return entry[1].get(mid)

        lock = self._provider_locks.setdefault(addr, asyncio.Lock())
        async with lock:
            # Re-check: another waiter may have just filled the cache while
            # we were acquiring the lock.
            entry = self._reports.get(addr)
            if entry is not None and entry[0] > self._clock():
                return entry[1].get(mid)

            endpoint = await self._endpoint_for(addr)
            if not endpoint:
                self._reports[addr] = (self._clock() + self._negative_ttl, {})
                return None

            try:
                report = await proxy_router_service.pingProvider(self._display_address(addr), endpoint)
            except Exception as exc:  # any transport/proxy error: negative-cache
                logger.warning("provider ping for api spec failed",
                               provider=addr, endpoint=endpoint, error=str(exc),
                               event_type="provider_api_spec_ping_failed")
                self._reports[addr] = (self._clock() + self._negative_ttl, {})
                return None

            specs: Dict[str, dict] = {}
            for model in (report or {}).get("models") or []:
                if not isinstance(model, dict):
                    continue
                model_key = str(model.get("modelId") or "").strip().lower()
                api = model.get("api")
                if model_key and isinstance(api, dict):
                    specs[model_key] = api
            self._reports[addr] = (self._clock() + self._ttl, specs)
            return specs.get(mid)

    def _unknown_provider_needs_forced_refresh(self, addr: str, now: float) -> bool:
        """An address absent from an otherwise-fresh list may simply be new
        on-chain since the last refresh. Force a refresh at most once per
        UNKNOWN_PROVIDER_FORCE_REFRESH_SECONDS so a burst of lookups for the
        same (or other) unknown providers doesn't hammer getProviders(); never
        fires while the last refresh failed (that's the negative-cache path)."""
        return (
            addr not in self._endpoints
            and self._endpoints_refreshed_at is not None
            and now - self._endpoints_refreshed_at >= UNKNOWN_PROVIDER_FORCE_REFRESH_SECONDS
        )

    async def _endpoint_for(self, addr: str) -> Optional[str]:
        now = self._clock()
        needs_refresh = self._endpoints_expire_at <= now or self._unknown_provider_needs_forced_refresh(addr, now)
        if needs_refresh:
            async with self._providers_lock:
                # Re-check: another waiter may have just refreshed the list.
                now = self._clock()
                if self._endpoints_expire_at <= now or self._unknown_provider_needs_forced_refresh(addr, now):
                    try:
                        providers: List[Any] = await proxy_router_service.getProviders()
                    except Exception as exc:
                        logger.warning("provider list fetch failed", error=str(exc),
                                       event_type="provider_api_spec_providers_failed")
                        # Back off instead of retrying on every miss during an
                        # outage; keep whatever endpoints/display we already know.
                        self._endpoints_expire_at = self._clock() + self._negative_ttl
                        return self._endpoints.get(addr)

                    fresh: Dict[str, str] = {}
                    display: Dict[str, str] = {}
                    for p in providers:
                        if not isinstance(p, dict):
                            continue
                        if p.get("IsDeleted") or p.get("isDeleted"):
                            continue
                        a = str(p.get("Address") or p.get("address") or "").strip().lower()
                        e = str(p.get("Endpoint") or p.get("endpoint") or "").strip()
                        if a and e:
                            fresh[a] = e
                            display[a] = str(p.get("Address") or p.get("address")).strip()
                    # Replace (not accumulate) so providers that disappeared
                    # from this refresh are pruned rather than kept forever.
                    self._endpoints = fresh
                    self._display = display
                    now = self._clock()
                    self._endpoints_expire_at = now + self._ttl
                    self._endpoints_refreshed_at = now
        return self._endpoints.get(addr)

    # Keep the address exactly as the proxy-router reported it (checksum case)
    # for the ping call; lowercase is only our cache key.
    def _display_address(self, addr: str) -> str:
        return self._display.get(addr, addr)


provider_api_spec_service = ProviderApiSpecService()
