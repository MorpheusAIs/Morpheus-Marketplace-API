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
"""
import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

import structlog

from src.core.config import settings
from src.services import proxy_router_service

logger = structlog.get_logger(__name__)


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
        # provider address (lowercase) -> the checksum-cased address as reported
        # by the proxy-router, kept per-instance so tests don't leak state.
        self._display: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        self._reports.clear()
        self._endpoints.clear()
        self._endpoints_expire_at = 0.0
        self._display.clear()

    async def get_spec(self, provider_address: str, model_id: str) -> Optional[dict]:
        """Return the provider-declared `api` block for model_id, or None."""
        addr = (provider_address or "").strip().lower()
        mid = (model_id or "").strip().lower()
        if not addr or not mid:
            return None

        async with self._lock:
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

    async def _endpoint_for(self, addr: str) -> Optional[str]:
        if self._endpoints_expire_at <= self._clock() or addr not in self._endpoints:
            try:
                providers: List[Any] = await proxy_router_service.getProviders()
            except Exception as exc:
                logger.warning("provider list fetch failed", error=str(exc),
                               event_type="provider_api_spec_providers_failed")
                providers = []
            fresh: Dict[str, str] = {}
            for p in providers:
                if not isinstance(p, dict):
                    continue
                a = str(p.get("Address") or p.get("address") or "").strip().lower()
                e = str(p.get("Endpoint") or p.get("endpoint") or "").strip()
                if a and e:
                    fresh[a] = e
                    self._display[a] = str(p.get("Address") or p.get("address")).strip()
            if fresh:
                self._endpoints = fresh
                self._endpoints_expire_at = self._clock() + self._ttl
        return self._endpoints.get(addr)

    # Keep the address exactly as the proxy-router reported it (checksum case)
    # for the ping call; lowercase is only our cache key.
    def _display_address(self, addr: str) -> str:
        return self._display.get(addr, addr)


provider_api_spec_service = ProviderApiSpecService()
