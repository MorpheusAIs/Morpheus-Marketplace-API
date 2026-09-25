import copy
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Optional

CANONICAL_FIELD = "web_search"
# A provider-native OpenAI field whose presence alone asks for web search; it is never rewritten or removed
ALIAS_FIELD = "web_search_options"
WEB_SEARCH_FIELDS = (CANONICAL_FIELD, ALIAS_FIELD)

INTENT_ENABLE = "web_search.enable"
INTENT_CITATIONS = "web_search.citations"
WEB_SEARCH_INTENTS = frozenset({INTENT_ENABLE, INTENT_CITATIONS})

KIND_BODY_PARAM = "body_param"
KIND_TEMPLATE_KWARG = "template_kwarg"
KIND_TOOL_ENTRY = "tool_entry"
TOOLS_PARAM = "tools"

WEB_SEARCH_TAG = "web-search-enabled"
MISMATCH_TOKEN = "web_search"


@dataclass
class WebSearchResult:
    applied: Dict[str, str] = field(default_factory=dict)
    unsupported: List[str] = field(default_factory=list)
    mismatch: bool = False
    touched: bool = False


def extract_web_search_intents(body: Dict[str, Any]) -> Dict[str, Any]:
    """Intents from the canonical web_search object, else from the alias; enabled=false yields nothing."""
    canonical = body.get(CANONICAL_FIELD)
    if isinstance(canonical, dict):
        if canonical.get("enabled") is not True:
            return {}
        intents: Dict[str, Any] = {INTENT_ENABLE: True}
        if canonical.get("citations") is True:
            intents[INTENT_CITATIONS] = True
        return intents
    if isinstance(body.get(ALIAS_FIELD), dict):
        return {INTENT_ENABLE: True}
    return {}


def web_search_disabled(body: Dict[str, Any]) -> bool:
    canonical = body.get(CANONICAL_FIELD)
    return isinstance(canonical, dict) and canonical.get("enabled") is False


def spec_denies_web_search(spec: Optional[Dict[str, Any]]) -> bool:
    """True only on an explicit capabilities.webSearch == false; absent means unknown."""
    if not isinstance(spec, dict):
        return False
    caps = spec.get("capabilities")
    return isinstance(caps, dict) and caps.get("webSearch") is False


def tags_claim_web_search(tags: Optional[Iterable[str]]) -> bool:
    return WEB_SEARCH_TAG in (tags or ())


IDENTITY_KEYS = ("type", "name", "id")


def _entry_present(entries: List[Any], value: Any) -> bool:
    """Whether value is already in entries, by identity keys when both carry them, else by equality.

    Providers key tools and plugins by type/name/id, so a client-configured {"id": "web", "max_results": 3} or a
    web_search tool with max_uses is the same entry, and a second copy would be rejected as a duplicate.
    """
    if value in entries:
        return True
    if not isinstance(value, dict):
        return False
    keys = [k for k in IDENTITY_KEYS if k in value]
    return bool(keys) and any(
        isinstance(e, dict) and all(k in e and e[k] == value[k] for k in keys) for e in entries
    )


def _merge_value(existing: Any, value: Any) -> Any:
    # A list binding (openrouter plugins) extends what the client sent; an object binding fills in only the keys
    # the client did not set, so an alias web_search_options survives a {} binding. Scalars overwrite.
    if isinstance(value, list) and isinstance(existing, list):
        return existing + [v for v in value if not _entry_present(existing, v)]
    if isinstance(value, dict) and isinstance(existing, dict):
        return {**value, **existing}
    return value


def _set_merged(body: Dict[str, Any], parts: List[str], value: Any) -> None:
    cur = body
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = _merge_value(cur.get(parts[-1]), value)


def _apply_tool_entry(body: Dict[str, Any], binding: Dict[str, Any]) -> bool:
    value = binding.get("value")
    if binding.get("param") != TOOLS_PARAM or not isinstance(value, dict):
        return False
    tools = body.get(TOOLS_PARAM)
    if tools is None:
        tools = []
        body[TOOLS_PARAM] = tools
    elif not isinstance(tools, list):
        return False
    if not _entry_present(tools, value):
        tools.append(copy.deepcopy(value))
    return True


def _apply_param(
    body: Dict[str, Any], binding: Dict[str, Any], requested: Any, protected_roots: FrozenSet[str]
) -> Optional[str]:
    param = binding.get("param")
    if not isinstance(param, str):
        return None
    parts = [p for p in param.split(".") if p]
    if not parts or parts[0] in protected_roots:
        return None
    if binding.get("kind") == KIND_TEMPLATE_KWARG and not param.startswith("chat_template_kwargs."):
        return None
    value = binding["value"] if binding.get("value") is not None else requested
    enum_values = binding.get("enumValues")
    if binding.get("paramType") == "enum" and isinstance(enum_values, list) and value not in enum_values:
        return None
    _set_merged(body, parts, copy.deepcopy(value))
    return param


def apply_web_search(
    body: Dict[str, Any],
    spec: Optional[Dict[str, Any]],
    protected_roots: FrozenSet[str],
    model_tags: Optional[Iterable[str]] = None,
) -> WebSearchResult:
    """Rewrite the canonical web_search request into the provider's bindings, in place.

    A model tagged web-search-enabled on chain whose provider spec says webSearch is false is a capability
    mismatch: nothing is applied, the body is left as sent and the caller reports it.
    """
    result = WebSearchResult()
    if web_search_disabled(body):
        body.pop(CANONICAL_FIELD, None)
        result.touched = True
        return result
    intents = extract_web_search_intents(body)
    if not intents:
        return result

    if tags_claim_web_search(model_tags) and spec_denies_web_search(spec):
        result.mismatch = True
        result.unsupported.extend(intents)
        return result
    if not isinstance(spec, dict):
        return result
    bindings = spec.get("bindings") if isinstance(spec.get("bindings"), dict) else {}

    for intent, requested in intents.items():
        binding = bindings.get(intent)
        if not isinstance(binding, dict):
            result.unsupported.append(intent)
            continue
        try:
            kind = binding.get("kind")
            if kind == KIND_TOOL_ENTRY:
                param = TOOLS_PARAM if _apply_tool_entry(body, binding) else None
            elif kind in (KIND_BODY_PARAM, KIND_TEMPLATE_KWARG):
                param = _apply_param(body, binding, requested, protected_roots)
            else:
                param = None
        except Exception:
            param = None
        if param is None:
            result.unsupported.append(intent)
        else:
            result.applied[intent] = param

    if result.applied:
        if CANONICAL_FIELD not in {p.split(".", 1)[0] for p in result.applied.values()}:
            body.pop(CANONICAL_FIELD, None)
        result.touched = True
    return result
