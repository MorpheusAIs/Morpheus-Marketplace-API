"""
Translate the canonical `reasoning` field into the provider-specific request
params declared by the session's provider (its proxy-router reports a
per-model `api` block with `bindings`: canonical intent -> param binding).

Contract (see docs/superpowers/specs/2026-09-09-api-presets-design.md in the
proxy-router worktree):
- canonical input: reasoning{enabled, effort, max_tokens} and the alias
  reasoning_effort ("none" -> disable);
- only body_param / template_kwarg bindings are applied (dotted paths in the
  chat-completions body); system_prompt / native_body_param are reported as
  unsupported;
- canonical fields are removed only when at least one intent was applied;
- no spec / no provider / flag off -> the body is left exactly as sent.
"""
import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import structlog

from src.core.config import settings
from ....db.database import get_db
from ....services.session_routing_service import session_routing_service
from src.services.provider_api_spec_service import provider_api_spec_service

logger = structlog.get_logger(__name__)

CANONICAL_FIELDS = ("reasoning", "reasoning_effort")
APPLIED_KINDS = ("body_param", "template_kwarg")

INTENT_DISABLE = "reasoning.disable"
INTENT_ENABLE = "reasoning.enable"
INTENT_EFFORT = "reasoning.effort"
INTENT_BUDGET = "reasoning.budget"


@dataclass
class TranslationResult:
    applied: Dict[str, str] = field(default_factory=dict)   # intent -> param path written
    unsupported: List[str] = field(default_factory=list)    # intents the provider cannot express
    stack: Optional[str] = None
    touched: bool = False

    def headers(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if self.stack:
            out["X-Morpheus-Provider-Stack"] = self.stack
        if self.applied:
            out["X-Morpheus-Translated"] = ";".join(f"{k}={v}" for k, v in self.applied.items())
        if self.unsupported:
            out["X-Morpheus-Unsupported"] = ";".join(self.unsupported)
        return out


def extract_intents(body: Dict[str, Any]) -> Dict[str, Any]:
    """Map the canonical fields in body to intent -> requested value."""
    intents: Dict[str, Any] = {}
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        enabled = reasoning.get("enabled")
        if enabled is False:
            intents[INTENT_DISABLE] = True
        elif enabled is True:
            intents[INTENT_ENABLE] = True
        effort = reasoning.get("effort")
        if isinstance(effort, str) and effort:
            intents[INTENT_EFFORT] = effort
        budget = reasoning.get("max_tokens")
        if isinstance(budget, int) and not isinstance(budget, bool) and budget >= 0:
            intents[INTENT_BUDGET] = budget
    alias = body.get("reasoning_effort")
    if isinstance(alias, str) and alias and not intents:
        if alias.lower() == "none":
            intents[INTENT_DISABLE] = True
        else:
            intents[INTENT_EFFORT] = alias
    return intents


def set_path(obj: Dict[str, Any], dotted: str, value: Any) -> None:
    """Write value at a dotted path, creating intermediate objects."""
    parts = [p for p in dotted.split(".") if p]
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def apply_spec(body: Dict[str, Any], spec: Optional[Dict[str, Any]]) -> TranslationResult:
    """Rewrite canonical intents in body according to spec['bindings'] (in place)."""
    result = TranslationResult()
    intents = extract_intents(body)
    if not intents or not isinstance(spec, dict):
        return result
    result.stack = spec.get("stack") or None
    bindings = spec.get("bindings") if isinstance(spec.get("bindings"), dict) else {}

    for intent, requested in intents.items():
        binding = bindings.get(intent)
        if not isinstance(binding, dict) or binding.get("kind") not in APPLIED_KINDS or not binding.get("param"):
            result.unsupported.append(intent)
            continue
        value = binding["value"] if binding.get("value") is not None else requested
        enum_values = binding.get("enumValues")
        if binding.get("paramType") == "enum" and isinstance(enum_values, list) and value not in enum_values:
            result.unsupported.append(intent)
            continue
        set_path(body, binding["param"], value)
        result.applied[intent] = binding["param"]

    if result.applied:
        # Don't clear a canonical field that a binding just wrote into (e.g.
        # a stack whose output param happens to be named "reasoning_effort").
        written_roots = {p.split(".", 1)[0] for p in result.applied.values()}
        for key in CANONICAL_FIELDS:
            if key not in written_roots:
                body.pop(key, None)
        result.touched = True
    return result


async def translate_for_session(body: Dict[str, Any], session_id: Optional[str], model_id: Optional[str]) -> TranslationResult:
    """Apply the session provider's spec to body (in place). No-op when disabled or unknown."""
    if not settings.REQUEST_TRANSLATION_ENABLED or not session_id or not model_id:
        return TranslationResult()
    if not extract_intents(body):
        return TranslationResult()
    try:
        async with get_db() as db:
            row = await session_routing_service.get_session_info(db, session_id)
        provider_address = getattr(row, "provider_address", None) if row is not None else None
        if not provider_address:
            return TranslationResult()
        spec = await provider_api_spec_service.get_spec(provider_address, model_id)
    except Exception as exc:  # translation must never break a request
        logger.warning("request translation lookup failed", session_id=session_id, error=str(exc),
                       event_type="request_translation_lookup_failed")
        return TranslationResult()
    work = copy.deepcopy(body)
    try:
        result = apply_spec(work, spec)
    except Exception as exc:  # translation must never break a request
        logger.warning("request translation apply failed", session_id=session_id, error=str(exc),
                       event_type="request_translation_apply_failed")
        return TranslationResult()
    if result.touched:
        body.clear()
        body.update(work)
    if result.touched or result.unsupported:
        logger.info("request translation applied", session_id=session_id, stack=result.stack,
                    applied=result.applied, unsupported=result.unsupported,
                    event_type="request_translation_applied")
    return result
