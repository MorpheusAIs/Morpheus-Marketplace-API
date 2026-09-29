import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

import structlog

from src.core.config import settings
from src.services import proxy_router_service

logger = structlog.get_logger(__name__)

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
        self._reports: Dict[str, tuple] = {}
        self._endpoints: Dict[str, str] = {}
        self._endpoints_expire_at: float = 0.0
        self._endpoints_attempted_at: float = 0.0
        self._display: Dict[str, str] = {}
        # Unlocked setdefault in get_spec is safe: no await between lookup and insert
        self._provider_locks: Dict[str, asyncio.Lock] = {}
        self._providers_lock = asyncio.Lock()

    def clear(self) -> None:
        self._reports.clear()
        self._endpoints.clear()
        self._endpoints_expire_at = 0.0
        self._endpoints_attempted_at = 0.0
        self._display.clear()
        self._provider_locks.clear()

    async def get_spec(self, provider_address: str, model_id: str) -> Optional[dict]:
        addr = (provider_address or "").strip().lower()
        mid = (model_id or "").strip().lower()
        if not addr or not mid:
            return None

        entry = self._reports.get(addr)
        if entry is not None and entry[0] > self._clock():
            return entry[1].get(mid)

        lock = self._provider_locks.setdefault(addr, asyncio.Lock())
        async with lock:
            entry = self._reports.get(addr)
            if entry is not None and entry[0] > self._clock():
                return entry[1].get(mid)

            endpoint = await self._endpoint_for(addr)
            if not endpoint:
                self._reports[addr] = (self._clock() + self._negative_ttl, {})
                return None

            try:
                report = await proxy_router_service.pingProvider(self._display_address(addr), endpoint)
            except Exception as exc:
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
        # Unknown addresses may be newly registered; rate-limit by last attempt so an outage cannot trigger a refresh per address
        return (
            addr not in self._endpoints
            and now - self._endpoints_attempted_at >= UNKNOWN_PROVIDER_FORCE_REFRESH_SECONDS
        )

    async def _endpoint_for(self, addr: str) -> Optional[str]:
        now = self._clock()
        needs_refresh = self._endpoints_expire_at <= now or self._unknown_provider_needs_forced_refresh(addr, now)
        if needs_refresh:
            async with self._providers_lock:
                now = self._clock()
                if self._endpoints_expire_at <= now or self._unknown_provider_needs_forced_refresh(addr, now):
                    self._endpoints_attempted_at = self._clock()
                    try:
                        providers: List[Any] = await proxy_router_service.getProviders()
                    except Exception as exc:
                        logger.warning("provider list fetch failed", error=str(exc),
                                       event_type="provider_api_spec_providers_failed")
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
                    self._endpoints = fresh
                    self._display = display
                    self._endpoints_expire_at = self._clock() + self._ttl
        return self._endpoints.get(addr)

    # Ping needs the checksum-cased address; lowercase is only the cache key
    def _display_address(self, addr: str) -> str:
        return self._display.get(addr, addr)


provider_api_spec_service = ProviderApiSpecService()
