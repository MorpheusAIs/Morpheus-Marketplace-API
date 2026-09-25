import json
from typing import Any, Dict, List, Optional

import structlog

from src.services.provider_api_spec_service import provider_api_spec_service

logger = structlog.get_logger(__name__)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _single_distinct(values: List[Any]) -> Optional[Any]:
    """The one value all entries share (by canonical JSON), or None when there are none or they differ."""
    distinct: Dict[str, Any] = {}
    for value in values:
        distinct.setdefault(_canonical(value), value)
    if len(distinct) == 1:
        return next(iter(distinct.values()))
    return None


def _thinking(spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    thinking = spec.get("thinking")
    if not isinstance(thinking, dict) or not isinstance(thinking.get("mode"), str) or not thinking["mode"]:
        return None
    out = {"mode": thinking["mode"]}
    default = thinking.get("default")
    if isinstance(default, str) and default:
        out["default"] = default
    return out


def spec_enrichment(model_id: str) -> Dict[str, Any]:
    """Fields for a GET /models row read from the provider-spec cache only (no ping).

    Attached only when every cached provider spec for the model reports the same capabilities; model_family
    and thinking are taken from those specs when they agree on them as well.
    """
    specs = provider_api_spec_service.cached_specs_for_model(model_id)
    caps_values = [s["capabilities"] for s in specs if isinstance(s.get("capabilities"), dict)]
    if not caps_values:
        return {}
    capabilities = _single_distinct(caps_values)
    if capabilities is None:
        logger.debug("provider specs disagree on model capabilities; not attaching",
                     model_id=model_id, provider_specs=len(specs),
                     distinct_values=len({_canonical(v) for v in caps_values}),
                     event_type="models_capabilities_disagree")
        return {}
    out: Dict[str, Any] = {"capabilities": capabilities}
    families = [s["modelFamily"] for s in specs if isinstance(s.get("modelFamily"), str) and s["modelFamily"]]
    family = _single_distinct(families)
    if family is not None:
        out["model_family"] = family
    thinkings = [t for t in (_thinking(s) for s in specs) if t is not None]
    thinking = _single_distinct(thinkings)
    if thinking is not None:
        out["thinking"] = thinking
    return out
