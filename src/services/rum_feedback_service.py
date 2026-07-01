"""
RUM feedback publisher for active-models curation.

Aggregates per-bid answerability observations from ``routed_sessions`` (which
now carry ``bid_id`` — see Phase 1) over a rolling window and publishes them as
``feedback/apigw-bid-health.json`` to the same S3 bucket the ``05-active_models``
Lambda reads. The Lambda uses this to NARROW its ``ping_provider`` universe:
ping proves a provider's router is reachable, this proves whether the bid's model
actually answers. See docs/active-models-rum-canary.md.

This publishes RUM only. The canary does NOT emit its own signal — it merely
forces usage of otherwise-cold models (a short session + tiny prompt), so a
canaried model's outcome shows up here as ordinary RUM. RUM is therefore the
single source of answerability signal in the feedback file.

Design notes:
- The gateway publishes OBSERVATIONS + categorized reasons only; it does not
  compute a health verdict (the Lambda owns curation + hysteresis + rollup).
- Signal is strictly subtractive: FAILED sessions are failure evidence; used,
  non-failed sessions are success evidence. Neither can add a bid the Lambda's
  ping universe did not already contain.
- Empty ``ACTIVE_MODELS_FEEDBACK_BUCKET`` disables the publisher entirely.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.logging_config import get_api_logger
from ..db.models import RoutedSession, SessionState
from ..services import proxy_router_service

logger = get_api_logger()


# Ordered failure taxonomy (specific → generic). Each entry maps substrings in a
# session's error_reason to a stable code + human-facing message/hint that feed a
# provider-facing status board. Mirrors the failover classifier so eligibility
# and health never disagree (see docs/active-models-rum-canary.md).
_ERROR_TAXONOMY: List[Dict[str, Any]] = [
    {
        "code": "adapter_not_found",
        "patterns": ["adapter not found"],
        "message": "session opened but the model/adapter is not served",
        "hint": "check the provider's model mapping / LiteLLM config",
    },
    {
        "code": "tee_attestation_failed",
        "patterns": [p.lower() for p in proxy_router_service.NON_RETRIABLE_ERROR_PATTERNS],
        "message": "TEE attestation was rejected",
        "hint": "check TEE/SecretVM attestation on the provider",
    },
    {
        "code": "session_open_failed",
        "patterns": ["no session id returned", "failed to create session", "failed to open session"],
        "message": "could not open a session on this bid",
        "hint": "bid may be stale on-chain / provider offline / stake issue",
    },
    {
        "code": "transport_connect",
        "patterns": [
            "failed to connect to provider",
            "failed to write to provider",
            "provider not found",
            "failed to create chat completions stream",
        ],
        "message": "provider router reachable but the request could not be delivered",
        "hint": "check the provider router → backend networking",
    },
    {
        "code": "transport_timeout",
        "patterns": ["read timed out", "timed out", "timeout"],
        "message": "the prompt timed out",
        "hint": "check model latency/capacity vs the request timeout",
    },
    {
        "code": "upstream_eof",
        "patterns": ["eof", "provider closed connection"],
        "message": "the backend dropped the connection mid-response",
        "hint": "check the backend for crashes/OOM/timeouts under load",
    },
    {
        "code": "upstream_5xx",
        "patterns": ["provider request failed", "internal server error", "bad gateway", "service unavailable"],
        "message": "the backend returned a server error",
        "hint": "check the backend logs (model load, quota, API keys)",
    },
    {
        "code": "empty_response",
        "patterns": ["empty response", "invalid response", "no response"],
        "message": "the model answered but returned an empty/invalid response",
        "hint": "check the model output config (chat template, max tokens)",
    },
]


def categorize_error(error_reason: Optional[str]) -> Dict[str, str]:
    """Map a session error_reason to a taxonomy {code, message, hint}."""
    text = (error_reason or "").lower()
    for entry in _ERROR_TAXONOMY:
        if any(p and p in text for p in entry["patterns"]):
            return {"code": entry["code"], "message": entry["message"], "hint": entry["hint"]}
    return {
        "code": "unknown",
        "message": "unclassified failure",
        "hint": "inspect the raw error_reason",
    }


def _modality_for_endpoint(endpoint: Optional[str]) -> str:
    ep = (endpoint or "").lower()
    if "embeddings" in ep:
        return "embeddings"
    if "audio/transcriptions" in ep or "/stt" in ep:
        return "stt"
    if "audio/speech" in ep or "/tts" in ep:
        return "tts"
    return "llm"


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    # Stored naive-UTC; render as explicit UTC.
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


class RumFeedbackService:
    """Aggregates per-bid RUM and publishes it to the active-models S3 bucket."""

    def __init__(self) -> None:
        self._s3_client = None

    @property
    def enabled(self) -> bool:
        return bool(settings.ACTIVE_MODELS_FEEDBACK_BUCKET)

    def _get_s3(self):
        if self._s3_client is None:
            import boto3  # local import: only needed when the publisher runs

            self._s3_client = boto3.client("s3", region_name=settings.AWS_REGION)
        return self._s3_client

    async def build_feedback(self, db: AsyncSession) -> Dict[str, Any]:
        """Aggregate per-bid RUM observations over the configured window."""
        window_hours = settings.RUM_FEEDBACK_WINDOW_HOURS
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        window_start = now - timedelta(hours=window_hours)
        failed = SessionState.FAILED.value

        # Coarse counts per bid: FAILED sessions are failures; non-failed sessions
        # that served at least one request (last_used_at set) are successes.
        counts_stmt = (
            select(
                RoutedSession.bid_id,
                func.max(RoutedSession.model_id).label("model_id"),
                func.max(RoutedSession.endpoint).label("endpoint"),
                func.count().filter(RoutedSession.state == failed).label("err"),
                func.count()
                .filter(and_(RoutedSession.state != failed, RoutedSession.last_used_at.isnot(None)))
                .label("ok"),
                func.max(RoutedSession.last_used_at).filter(RoutedSession.state != failed).label("last_ok"),
                func.max(RoutedSession.updated_at).filter(RoutedSession.state == failed).label("last_err"),
            )
            .where(and_(RoutedSession.bid_id.isnot(None), RoutedSession.updated_at >= window_start))
            .group_by(RoutedSession.bid_id)
        )

        # FAILED error_reasons (the minority) for per-code counts + latest reason.
        failed_stmt = (
            select(RoutedSession.bid_id, RoutedSession.error_reason, RoutedSession.updated_at)
            .where(
                and_(
                    RoutedSession.bid_id.isnot(None),
                    RoutedSession.state == failed,
                    RoutedSession.updated_at >= window_start,
                )
            )
            .order_by(RoutedSession.updated_at.desc())
        )

        counts_rows = (await db.execute(counts_stmt)).all()
        failed_rows = (await db.execute(failed_stmt)).all()

        # Per bid: tally error codes and keep the most recent (rows are DESC).
        code_counts: Dict[str, Dict[str, int]] = {}
        latest_error: Dict[str, Dict[str, str]] = {}
        for bid_id, error_reason, _updated in failed_rows:
            cat = categorize_error(error_reason)
            code = cat["code"]
            code_counts.setdefault(bid_id, {})
            code_counts[bid_id][code] = code_counts[bid_id].get(code, 0) + 1
            if bid_id not in latest_error:
                latest_error[bid_id] = cat

        bids: List[Dict[str, Any]] = []
        for row in counts_rows:
            bid_id = row.bid_id
            bids.append(
                {
                    "bid_id": bid_id,
                    "model_id": row.model_id,
                    "provider": None,  # resolved at rollup by the Lambda
                    "modality": _modality_for_endpoint(row.endpoint),
                    "rum": {
                        "ok": int(row.ok or 0),
                        "err": int(row.err or 0),
                        "last_ok": _iso(row.last_ok),
                        "last_err": _iso(row.last_err),
                        "last_error": latest_error.get(bid_id),
                        "counts": code_counts.get(bid_id, {}),
                    },
                }
            )

        return {
            "generated_at": _iso(now),
            "window_hours": window_hours,
            "source": "apigw",
            "bids": bids,
        }

    async def publish(self, payload: Dict[str, Any]) -> None:
        """Write the feedback JSON to S3 (off the event loop)."""
        body = json.dumps(payload, indent=2).encode("utf-8")
        bucket = settings.ACTIVE_MODELS_FEEDBACK_BUCKET
        key = settings.ACTIVE_MODELS_FEEDBACK_KEY

        def _put() -> None:
            self._get_s3().put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
            )

        await asyncio.to_thread(_put)


rum_feedback_service = RumFeedbackService()
