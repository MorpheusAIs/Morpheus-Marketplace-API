"""Token usage extraction for embeddings billing finalize."""

from __future__ import annotations

from typing import Any, Tuple


def extract_embeddings_usage(response_data: Any) -> Tuple[int, int, str]:
    """Pull embedding token counts from a proxy/OpenAI-style response body.

    Prefer the node's chat-style ``usage_from_provider`` when present; fall back
    to the OpenAI-compatible ``usage`` object that the embeddings path returns
    today. Returns ``(tokens_input, tokens_total, source)`` where source is the
    field name used, or ``"none"`` when neither yields countable tokens.
    """
    if not isinstance(response_data, dict):
        return 0, 0, "none"

    for key in ("usage_from_provider", "usage"):
        usage = response_data.get(key)
        if not isinstance(usage, dict):
            continue
        try:
            tokens_input = int(usage.get("prompt_tokens") or 0)
        except (TypeError, ValueError):
            tokens_input = 0
        try:
            tokens_total = int(usage.get("total_tokens") or 0)
        except (TypeError, ValueError):
            tokens_total = 0
        if tokens_total <= 0:
            tokens_total = tokens_input
        if tokens_input > 0 or tokens_total > 0:
            return tokens_input, tokens_total, key

    return 0, 0, "none"
