import contextvars
import copy
import re
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

# Untrusted provider specs must never rewrite these routing fields
PROTECTED_ROOTS = frozenset({"messages", "stream", "session_id", "request_id", "model", "tools", "tool_choice"})

_HEADER_SAFE = re.compile(r"[^A-Za-z0-9._/-]")
_HEADER_ELEMENT_MAX = 128


def _header_token(value: Any) -> str:
    return _HEADER_SAFE.sub("", str(value))[:_HEADER_ELEMENT_MAX]


# Never reset: each ASGI request and its stream generator run in one task, so this cannot leak across requests
_TRANSLATION_ACTIVE: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "request_translation_active", default=False
)

_TRANSLATION_TRUTHY = {"1", "true", "yes", "on"}
_TRANSLATION_FALSY = {"0", "false", "no", "off"}


def _parse_translation_header(raw: Optional[str]) -> Optional[bool]:
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in _TRANSLATION_TRUTHY:
        return True
    if normalized in _TRANSLATION_FALSY:
        return False
    return None


def translation_requested(headers) -> bool:
    mode = settings.REQUEST_TRANSLATION_MODE
    if mode == "off":
        return False
    flag = _parse_translation_header(headers.get(settings.REQUEST_TRANSLATION_HEADER))
    if mode == "header":
        return flag is True
    return flag is not False


def set_request_translation(flag: bool) -> "contextvars.Token[bool]":
    return _TRANSLATION_ACTIVE.set(flag)


def request_translation_active() -> bool:
    return _TRANSLATION_ACTIVE.get()


def translation_gate_headers() -> Dict[str, str]:
    if settings.REQUEST_TRANSLATION_MODE == "off":
        return {}
    return {
        "X-Morpheus-Translation": "on" if request_translation_active() else "off",
        "X-Morpheus-Translation-Mode": settings.REQUEST_TRANSLATION_MODE,
    }


@dataclass
class TranslationResult:
    applied: Dict[str, str] = field(default_factory=dict)
    unsupported: List[str] = field(default_factory=list)
    stack: Optional[str] = None
    touched: bool = False

    def headers(self) -> Dict[str, str]:
        # Untrusted provider values: sanitize against CRLF header injection and encoding errors
        out: Dict[str, str] = {}
        if self.stack:
            stack = _header_token(self.stack)
            if stack:
                out["X-Morpheus-Provider-Stack"] = stack
        if self.applied:
            pairs = []
            for intent, param in self.applied.items():
                intent_tok, param_tok = _header_token(intent), _header_token(param)
                if intent_tok and param_tok:
                    pairs.append(f"{intent_tok}={param_tok}")
            if pairs:
                out["X-Morpheus-Translated"] = ";".join(pairs)
        if self.unsupported:
            tokens = [t for t in (_header_token(u) for u in self.unsupported) if t]
            if tokens:
                out["X-Morpheus-Unsupported"] = ";".join(tokens)
        return out


def extract_intents(body: Dict[str, Any]) -> Dict[str, Any]:
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
        try:
            param = binding["param"]
            parts = [p for p in param.split(".") if p]
            if not parts or parts[0] in PROTECTED_ROOTS:
                result.unsupported.append(intent)
                continue
            if binding.get("kind") == "template_kwarg" and not param.startswith("chat_template_kwargs."):
                result.unsupported.append(intent)
                continue
            value = binding["value"] if binding.get("value") is not None else requested
            enum_values = binding.get("enumValues")
            if binding.get("paramType") == "enum" and isinstance(enum_values, list) and value not in enum_values:
                result.unsupported.append(intent)
                continue
            set_path(body, param, value)
            result.applied[intent] = param
        except Exception:
            result.unsupported.append(intent)

    if result.applied:
        written_roots = {p.split(".", 1)[0] for p in result.applied.values()}
        for key in CANONICAL_FIELDS:
            if key not in written_roots:
                body.pop(key, None)
        result.touched = True
    return result


async def translate_for_session(body: Dict[str, Any], session_id: Optional[str], model_id: Optional[str]) -> TranslationResult:
    if not request_translation_active() or not session_id or not model_id:
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
    except Exception as exc:  # best-effort: never fail the request
        logger.warning("request translation lookup failed", session_id=session_id, error=str(exc),
                       event_type="request_translation_lookup_failed")
        return TranslationResult()
    work = copy.deepcopy(body)
    try:
        result = apply_spec(work, spec)
    except Exception as exc:
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
