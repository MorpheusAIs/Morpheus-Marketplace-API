from typing import Any, Dict, Optional

# Wire ints from the proxy-router are int64 in (0, 1<<31); anything else is treated as unknown
_INT_MAX = 1 << 31


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value <= 0 or value >= _INT_MAX:
        return None
    return value


def read_context(spec: Optional[Dict[str, Any]]) -> Optional[int]:
    """capabilities.context from a provider spec, reported only; absent or invalid is None."""
    if not isinstance(spec, dict):
        return None
    caps = spec.get("capabilities")
    if not isinstance(caps, dict):
        return None
    return _positive_int(caps.get("context"))
