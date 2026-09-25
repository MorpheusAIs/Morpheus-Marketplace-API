from typing import Any, Dict, FrozenSet, Iterable, List, Optional

import structlog

from .request_translation import _header_token

logger = structlog.get_logger(__name__)

HEADER = "X-Morpheus-Unlisted-Params"

# https://platform.openai.com/docs/api-reference/chat/create
OPENAI_CHAT_PARAMS: FrozenSet[str] = frozenset({
    "model", "messages", "temperature", "top_p", "n", "stream", "stream_options", "stop", "max_tokens",
    "max_completion_tokens", "presence_penalty", "frequency_penalty", "logit_bias", "logprobs", "top_logprobs",
    "user", "seed", "response_format", "tools", "tool_choice", "parallel_tool_calls", "reasoning_effort",
})
# Routing keys the chat handlers strip before forwarding; the provider never sees them at all
ROUTING_FIELDS: FrozenSet[str] = frozenset({"session_id", "request_id"})
# Canonical fields the gateway consumes and rewrites; the provider never sees them by these names
GATEWAY_FIELDS: FrozenSet[str] = frozenset({"reasoning", "reasoning_effort", "web_search"}) | ROUTING_FIELDS
# The anthropic stack lists Anthropic Messages names (system, stop_sequences, top_k, ...) while the gateway
# receives OpenAI chat-completions bodies that the proxy-router reshapes, so the two vocabularies cannot be
# compared key for key and the check would flag nearly everything. Skipped entirely.
SKIP_STACKS: FrozenSet[str] = frozenset({"anthropic"})


def binding_roots(spec: Dict[str, Any]) -> FrozenSet[str]:
    """Top-level request keys any binding of the spec writes to."""
    bindings = spec.get("bindings")
    if not isinstance(bindings, dict):
        return frozenset()
    roots = set()
    for binding in bindings.values():
        param = binding.get("param") if isinstance(binding, dict) else None
        if isinstance(param, str):
            parts = [p for p in param.split(".") if p]
            if parts:
                roots.add(parts[0])
    return frozenset(roots)


def unlisted_params(request_keys: Iterable[str], spec: Optional[Dict[str, Any]]) -> List[str]:
    """Sorted top-level request keys the provider spec's parameters list does not account for.

    Empty when the spec lists no parameters or its stack speaks another vocabulary. Report only: the
    caller never drops or rejects these keys.
    """
    if not isinstance(spec, dict) or spec.get("stack") in SKIP_STACKS:
        return []
    parameters = spec.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        return []
    listed = {p for p in parameters if isinstance(p, str)}
    known = OPENAI_CHAT_PARAMS | GATEWAY_FIELDS | listed | binding_roots(spec)
    return sorted(k for k in request_keys if isinstance(k, str) and k not in known)


def unlisted_headers(body: Dict[str, Any], spec: Optional[Dict[str, Any]]) -> Dict[str, str]:
    unlisted = unlisted_params(body.keys(), spec)
    tokens = [t for t in (_header_token(k) for k in unlisted) if t]
    if not tokens:
        return {}
    logger.info("request carries params the provider spec does not list", stack=spec.get("stack"),
                unlisted=unlisted, event_type="request_translation_unlisted_params")
    return {HEADER: ";".join(tokens)}
