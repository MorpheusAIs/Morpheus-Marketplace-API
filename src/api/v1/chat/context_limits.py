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


# A media part costs the provider tokens by its resolution or duration, not by the length of its inline
# base64: a 300 KB image would read as ~100k tokens at 4 characters each. It counts this fixed amount
# instead, the low end of what providers bill (OpenAI's low-detail image is 85 tokens), so an inline image
# never eats the context-derived max_tokens ceiling by its payload size. The low figure can leave more room
# than a large image really takes; the provider enforces its own window then, as it does without the clamp.
MEDIA_PART_TOKENS = 85

# String fields of a content part that the model reads as text, whatever the part's type says
_TEXT_FIELDS = ("text", "refusal")


def _without_media(messages: Any) -> Tuple[Any, int]:
    """messages with every content part reduced to its type and text fields, and how many parts carried no
    text (media: image_url, input_audio, file, ...). Classified by payload, not by the type label, so text
    counts wherever it sits and a media object is never serialized."""
    if not isinstance(messages, list):
        return messages, 0
    out: List[Any] = []
    media = 0
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            parts: List[Any] = []
            for part in content:
                if not isinstance(part, dict):
                    parts.append(part)
                    continue
                reduced = {"type": part.get("type")}
                for name in _TEXT_FIELDS:
                    if isinstance(part.get(name), str):
                        reduced[name] = part[name]
                if len(reduced) == 1:
                    media += 1
                parts.append(reduced)
            message = {**message, "content": parts}
        out.append(message)
    return out, media


def estimate_prompt_tokens(messages: Any) -> int:
    """Rough prompt size in tokens: ~4 characters per token over the serialized messages, with each media
    part counted as MEDIA_PART_TOKENS instead of its payload.

    Deliberately crude and tokenizer-free; this is the single place to swap in a real tokenizer.
    """
    text, media = _without_media(messages)
    try:
        serialized = json.dumps(text, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = str(text)
    return math.ceil(len(serialized) / 4) + media * MEDIA_PART_TOKENS


@dataclass
class ContextLimitResult:
    context: Optional[int] = None
    max_output: Optional[int] = None
    estimated_prompt_tokens: Optional[int] = None
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
    that leaves at least one token. A prompt that does not fit is never refused here: the request is forwarded
    and the provider enforces its own window; max_tokens is still clamped to maxOutput.
    """
    result = ContextLimitResult()
    result.context, result.max_output = read_limits(spec)
    if result.context is None and result.max_output is None:
        return result

    if messages is not None:
        result.estimated_prompt_tokens = estimate_prompt_tokens(messages)

    ceiling = result.max_output
    if ceiling is None and result.context is not None and result.estimated_prompt_tokens is not None:
        remaining = result.context - result.estimated_prompt_tokens
        if remaining >= 1:
            ceiling = remaining
    if ceiling is None:
        return result

    for name in MAX_TOKEN_FIELDS:
        # A client value outside the wire range is left for the provider to reject; echoing it in the clamp
        # header would let a 4000-digit int blow past reverse-proxy header buffers
        value = _positive_int(body.get(name))
        if value is None:
            continue
        if value > ceiling:
            body[name] = ceiling
            result.clamped.append((name, value, ceiling))
            result.touched = True
    return result
