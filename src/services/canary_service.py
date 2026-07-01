"""
Canary — forces usage of orphan models so RUM can score them.

The 05-active_models Lambda starts from the broad ``ping_provider`` universe and
NARROWS it using the gateway's RUM feedback file. RUM only sees models organic
traffic actually exercises, so a model nobody happens to call stays invisible —
neither confirmed answerable nor demoted. The canary closes that gap: each sweep
it finds "orphan" models (no session opened in the look-back window) and opens a
short, real session + tiny prompt against each. That attempt flows through the
exact same session/prompt path organic traffic uses, so its outcome is captured
as ordinary RUM (see ``rum_feedback_service``). The canary itself emits NO
separate signal — it is purely a usage trigger. RUM remains the single source of
answerability signal in the feedback file.

Key properties:
- Short session (``CANARY_SESSION_DURATION_SECONDS``, default 300s = the on-chain
  ``MIN_SESSION_DURATION`` floor) that expires naturally — no early close, no MOR
  lock.
- An open failure is NOT recorded as RUM: an unreachable/unopenable provider is
  already excluded upstream by ``ping_provider``. The canary's real value is the
  reachable-but-not-answering case (open succeeds, prompt fails), which lands a
  FAILED row -> RUM ``err`` (e.g. an "adapter not found" dead bid).
- Config-gated (``CANARY_ENABLED``); a safe no-op when disabled.

See docs/active-models-rum-canary.md.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.direct_model_service import direct_model_service
from ..core.logging_config import get_api_logger
from ..db.database import get_db
from ..db.models import RoutedSession
from ..services import proxy_router_service
from ..services.session_routing_service import session_routing_service

logger = get_api_logger()

# A tiny, cheap probe. max_tokens=1 keeps the paid work minimal — we only need to
# prove the bid answers, not get a useful completion.
_CANARY_PROMPT = "ping"
_CANARY_MAX_TOKENS = 1
# Hard ceiling on a single probe's prompt (open excluded) so one hung provider
# can't stall the sweep. Deliberately far shorter than the user-facing chat
# timeout — a canary that doesn't answer promptly is itself a failure signal.
_CANARY_PROBE_TIMEOUT_SECONDS = 30

_SUPPORTED_MODALITIES = ("llm", "embeddings")


def _modality_for_model_type(model_type: Optional[str]) -> str:
    """Map an active-models ModelType to a probe modality."""
    t = (model_type or "").lower()
    if "embed" in t:
        return "embeddings"
    return "llm"


class CanaryService:
    """Sweeps orphan models and forces a short probe so RUM can score them."""

    @property
    def enabled(self) -> bool:
        return bool(settings.CANARY_ENABLED)

    async def _universe(self) -> List[Dict[str, str]]:
        """
        All non-deleted models we know how to probe, as
        ``[{id, name, modality, model_type}]``. The universe is the same
        active-models list the gateway routes against; unsupported modalities
        (no probe defined yet) are skipped.
        """
        raw = await direct_model_service.get_raw_models_data()
        universe: List[Dict[str, str]] = []
        for m in raw:
            if m.get("IsDeleted", False):
                continue
            model_id = m.get("Id")
            if not model_id:
                continue
            modality = _modality_for_model_type(m.get("ModelType"))
            if modality not in _SUPPORTED_MODALITIES:
                continue
            universe.append(
                {
                    "id": model_id,
                    "name": m.get("Name"),
                    "modality": modality,
                    # Drives endpoint selection in the open path (and thus the
                    # endpoint RUM reads modality back from).
                    "model_type": "EMBEDDINGS" if modality == "embeddings" else "LLM",
                }
            )
        return universe

    async def _recently_seen_model_ids(self, db: AsyncSession) -> set:
        """
        model_ids that had a session opened within the sweep window (organic OR a
        prior canary). Any such model is NOT an orphan — organic traffic already
        exercised it, or the last canary did. This makes the canary self-limiting:
        a model it probes counts as "seen" until the window rolls past.
        """
        window_start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            hours=settings.CANARY_SWEEP_INTERVAL_HOURS
        )
        rows = await db.execute(
            select(RoutedSession.model_id)
            .where(RoutedSession.created_at >= window_start)
            .group_by(RoutedSession.model_id)
        )
        return {r[0] for r in rows.all()}

    async def sweep(self) -> Dict[str, int]:
        """Probe every orphan model once (bounded concurrency)."""
        universe = await self._universe()
        async with get_db() as db:
            seen = await self._recently_seen_model_ids(db)
        orphans = [m for m in universe if m["id"] not in seen]

        logger.info(
            "Canary sweep starting",
            universe=len(universe),
            seen=len(seen),
            orphans=len(orphans),
            window_hours=settings.CANARY_SWEEP_INTERVAL_HOURS,
            event_type="canary_sweep_start",
        )
        if not orphans:
            return {"universe": len(universe), "orphans": 0, "ok": 0, "failed": 0, "open_failed": 0}

        sem = asyncio.Semaphore(max(1, settings.CANARY_MAX_CONCURRENCY))
        results = await asyncio.gather(
            *(self._probe(sem, m) for m in orphans), return_exceptions=True
        )

        tally = {"ok": 0, "failed": 0, "open_failed": 0}
        for r in results:
            if isinstance(r, Exception):
                # Defensive: _probe already catches its own errors.
                tally["open_failed"] += 1
            else:
                tally[r] = tally.get(r, 0) + 1

        summary = {"universe": len(universe), "orphans": len(orphans), **tally}
        logger.info("Canary sweep complete", event_type="canary_sweep_complete", **summary)
        return summary

    async def _probe(self, sem: asyncio.Semaphore, model: Dict[str, str]) -> str:
        async with sem:
            probe_logger = logger.bind(
                model_id=model["id"],
                model_name=model["name"],
                modality=model["modality"],
            )

            # 1) Open a short, tracked session, created already assigned
            #    (active_requests=1) so no organic request claims it mid-probe.
            try:
                session_id = await session_routing_service.open_probe_session(
                    model_id=model["id"],
                    model_name=model["name"],
                    model_type=model["model_type"],
                    session_duration=settings.CANARY_SESSION_DURATION_SECONDS,
                )
            except Exception as e:
                # Unreachable/unopenable provider is already excluded upstream by
                # ping_provider, so there's nothing for RUM to add here.
                probe_logger.info(
                    "Canary open failed (provider already excluded upstream by ping)",
                    error=str(e),
                    event_type="canary_open_failed",
                )
                return "open_failed"

            # 2) Send a tiny prompt, bounded so one hung provider can't stall the
            #    sweep.
            try:
                await asyncio.wait_for(
                    self._send(session_id, model["modality"]),
                    timeout=_CANARY_PROBE_TIMEOUT_SECONDS,
                )
                ok, reason = True, None
            except asyncio.TimeoutError:
                ok, reason = False, "canary probe timed out"
            except Exception as e:
                ok, reason = False, str(e)

            # 3) Record the outcome as RUM WITHOUT an early on-chain close — the
            #    short session expires naturally (no MOR lock).
            async with get_db() as db:
                await session_routing_service.complete_probe_session(
                    db, session_id, ok=ok, reason=reason
                )

            if ok:
                probe_logger.info(
                    "Canary probe ok",
                    session_id=session_id,
                    event_type="canary_probe_ok",
                )
                return "ok"
            probe_logger.warning(
                "Canary probe failed",
                session_id=session_id,
                reason=reason,
                event_type="canary_probe_failed",
            )
            return "failed"

    async def _send(self, session_id: str, modality: str):
        if modality == "embeddings":
            return await proxy_router_service.embeddings(
                session_id=session_id,
                input_data=_CANARY_PROMPT,
            )
        return await proxy_router_service.chatCompletions(
            session_id=session_id,
            messages=[{"role": "user", "content": _CANARY_PROMPT}],
            max_tokens=_CANARY_MAX_TOKENS,
        )


canary_service = CanaryService()
