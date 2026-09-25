import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

MAX_TOKEN_FIELDS = ("max_tokens", "max_completion_tokens")

# Wire ints from the proxy-router are int64 in (0, 1<<31); anything else is treated as unknown
_INT_MAX = 1 << 31


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value <= 0 or value >= _INT_MAX:
        return None
    return value


def has_max_token_fields(body: Dict[str, Any]) -> bool:
    return any(k in body for k in MAX_TOKEN_FIELDS)


def read_limits(spec: Optional[Dict[str, Any]]) -> Tuple[Optional[int], Optional[int]]:
    """Return (context, maxOutput) from a provider spec's capabilities block; absent or invalid values are None."""
    if not isinstance(spec, dict):
        return None, None
    caps = spec.get("capabilities")
    if not isinstance(caps, dict):
        return None, None
    return _positive_int(caps.get("context")), _positive_int(caps.get("maxOutput"))


def estimate_prompt_tokens(messages: Any) -> int:
    """Rough prompt size in tokens: ~4 characters per token over the serialized messages.

    Deliberately crude and tokenizer-free; this is the single place to swap in a real tokenizer.
    """
    try:
        serialized = json.dumps(messages, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = str(messages)
    return math.ceil(len(serialized) / 4)


@dataclass
class ContextLimitResult:
    context: Optional[int] = None
    max_output: Optional[int] = None
    estimated_prompt_tokens: Optional[int] = None
    # Prompt estimate alone exceeds the window; the caller decides whether to refuse the request
    prompt_exceeds: bool = False
    clamped: List[Tuple[str, int, int]] = field(default_factory=list)
    touched: bool = False

    def headers(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if self.context is not None:
            out["X-Morpheus-Context-Window"] = str(self.context)
        if self.clamped:
            out["X-Morpheus-Max-Tokens-Clamped"] = ";".join(f"{src}->{dst}" for _, src, dst in self.clamped)
        return out


def apply_context_limits(
    body: Dict[str, Any],
    spec: Optional[Dict[str, Any]],
    messages: Optional[Any] = None,
) -> ContextLimitResult:
    """Clamp max_tokens / max_completion_tokens in body to what the provider spec allows.

    The ceiling is capabilities.maxOutput, or context minus the prompt estimate when maxOutput is absent and
    that leaves at least one token. A prompt estimate above context is reported, not rewritten; max_tokens is
    still clamped to maxOutput so a retry that forwards the body anyway respects the provider's output cap.
    """
    result = ContextLimitResult()
    result.context, result.max_output = read_limits(spec)
    if result.context is None and result.max_output is None:
        return result

    if messages is not None:
        result.estimated_prompt_tokens = estimate_prompt_tokens(messages)
        if result.context is not None and result.estimated_prompt_tokens > result.context:
            result.prompt_exceeds = True

    ceiling = result.max_output
    if ceiling is None and result.context is not None and result.estimated_prompt_tokens is not None:
        remaining = result.context - result.estimated_prompt_tokens
        if remaining >= 1:
            ceiling = remaining
    if ceiling is None:
        return result

    for name in MAX_TOKEN_FIELDS:
        value = body.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if value > ceiling:
            body[name] = ceiling
            result.clamped.append((name, value, ceiling))
            result.touched = True
    return result
