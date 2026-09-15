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

# Roots a binding must never redirect: overwriting any of these could reroute
# messages, disable streaming, or hijack session/model/tool routing embedded
# in the request. Provider specs are untrusted data (see apply_spec).
PROTECTED_ROOTS = frozenset({"messages", "stream", "session_id", "request_id", "model", "tools", "tool_choice"})

_HEADER_SAFE = re.compile(r"[^A-Za-z0-9._/-]")
_HEADER_ELEMENT_MAX = 128


def _header_token(value: Any) -> str:
    """Reduce an untrusted string to a header-safe token (may be empty)."""
    return _HEADER_SAFE.sub("", str(value))[:_HEADER_ELEMENT_MAX]


# --- Per-request translation gating (ruling H1/H2) ---------------------------
#
# The decision is made ONCE per request, in index.py's create_chat_completion
# -- the only place with access to the incoming Request/its headers -- via
# translation_requested() below, then published here with
# set_request_translation() for the rest of the request to read back with
# request_translation_active().
#
# A ContextVar is safe for this: each ASGI request is driven by its own
# asyncio Task with its own copy of the current Context (uvicorn/Starlette
# start a fresh Task per request), and the streaming response generator for
# that request runs inside that same Task. So a value set here can never
# leak into a different request's Task, and this never has to be reset.
_TRANSLATION_ACTIVE: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "request_translation_active", default=False
)

_TRANSLATION_TRUTHY = {"1", "true", "yes", "on"}
_TRANSLATION_FALSY = {"0", "false", "no", "off"}


def _parse_translation_header(raw: Optional[str]) -> Optional[bool]:
    """True/False for a recognized truthy/falsy token (case-insensitive,
    trimmed); None for absence or any other value -- ruling H1 treats an
    unrecognized value as if the header were absent."""
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in _TRANSLATION_TRUTHY:
        return True
    if normalized in _TRANSLATION_FALSY:
        return False
    return None


def translation_requested(headers) -> bool:
    """Decide whether this request should be translated, from
    settings.REQUEST_TRANSLATION_MODE and the opt-in/opt-out header named by
    settings.REQUEST_TRANSLATION_HEADER:

    - off:     never.
    - header:  only when the header carries a truthy value.
    - always:  every request, unless the header carries a falsy value.
    """
    mode = settings.REQUEST_TRANSLATION_MODE
    if mode == "off":
        return False
    flag = _parse_translation_header(headers.get(settings.REQUEST_TRANSLATION_HEADER))
    if mode == "header":
        return flag is True
    return flag is not False  # mode == "always"


def set_request_translation(flag: bool) -> "contextvars.Token[bool]":
    """Publish this request's translation decision (see _TRANSLATION_ACTIVE
    above). Returns the contextvars Token purely so tests can reset it;
    request handling itself never needs to."""
    return _TRANSLATION_ACTIVE.set(flag)


def request_translation_active() -> bool:
    """This request's translation decision, as published by
    set_request_translation()."""
    return _TRANSLATION_ACTIVE.get()


def translation_gate_headers() -> Dict[str, str]:
    """The X-Morpheus-Translation* gate headers for this request, or {} when
    settings.REQUEST_TRANSLATION_MODE == "off" (translation fully disabled).

    Single source for both the success path (index.py's
    _prepare_translation_headers) and the ChatError error path (main.py's
    chat_error_handler) -- both must report the same gate headers for a
    given request, and both run in the request's own task/context, so
    request_translation_active() reads back whatever this request already
    published via set_request_translation()."""
    if settings.REQUEST_TRANSLATION_MODE == "off":
        return {}
    return {
        "X-Morpheus-Translation": "on" if request_translation_active() else "off",
        "X-Morpheus-Translation-Mode": settings.REQUEST_TRANSLATION_MODE,
    }


@dataclass
class TranslationResult:
    applied: Dict[str, str] = field(default_factory=dict)   # intent -> param path written
    unsupported: List[str] = field(default_factory=list)    # intents the provider cannot express
    stack: Optional[str] = None
    touched: bool = False

    def headers(self) -> Dict[str, str]:
        # spec["stack"], intent names and binding["param"] all originate from
        # an untrusted provider report — sanitize every element before it can
        # reach StreamingResponse's header encoding (UnicodeEncodeError) or a
        # raw socket write (CRLF header injection).
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
        # A malformed binding (e.g. a non-string param) must only take down
        # its own intent, not the intents that apply cleanly.
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
        # Don't clear a canonical field that a binding just wrote into (e.g.
        # a stack whose output param happens to be named "reasoning_effort").
        written_roots = {p.split(".", 1)[0] for p in result.applied.values()}
        for key in CANONICAL_FIELDS:
            if key not in written_roots:
                body.pop(key, None)
        result.touched = True
    return result


async def translate_for_session(body: Dict[str, Any], session_id: Optional[str], model_id: Optional[str]) -> TranslationResult:
    """Apply the session provider's spec to body (in place). No-op when this
    request's translation is inactive (request_translation_active() is
    False) or session_id/model_id is unknown."""
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
