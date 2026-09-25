import json
from typing import Annotated, Any, Dict, List, Optional

import structlog
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from src.schemas.openai import ModelCapabilities
from src.services.provider_api_spec_service import provider_api_spec_service

logger = structlog.get_logger(__name__)

# Provider specs are untrusted and republished on the unauthenticated models list: only declared fields, no
# coercion, bounded strings and lists
_STR_MAX = 64
_LIST_MAX = 16
_BoundedStr = Annotated[str, StringConstraints(min_length=1, max_length=_STR_MAX)]
_BoundedStrList = Annotated[List[_BoundedStr], Field(max_length=_LIST_MAX)]


class _ModalitiesProjection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input: Optional[_BoundedStrList] = None
    output: Optional[_BoundedStrList] = None


class _CapabilitiesProjection(ModelCapabilities):
    model_config = ConfigDict(extra="ignore", str_max_length=_STR_MAX)

    modalities: Optional[_ModalitiesProjection] = None
    structured: Optional[_BoundedStrList] = None


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


def _bounded_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and 0 < len(value) <= _STR_MAX else None


def _capabilities(spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    caps = spec.get("capabilities")
    if not isinstance(caps, dict):
        return None
    try:
        projected = _CapabilitiesProjection.model_validate(caps, strict=True).model_dump(exclude_none=True)
    except ValidationError as exc:
        logger.debug("provider spec capabilities rejected; not attaching", stack=spec.get("stack"),
                     errors=exc.error_count(), event_type="models_capabilities_invalid")
        return None
    return projected or None


def _thinking(spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    thinking = spec.get("thinking")
    if not isinstance(thinking, dict):
        return None
    mode = _bounded_str(thinking.get("mode"))
    if mode is None:
        return None
    out = {"mode": mode}
    default = _bounded_str(thinking.get("default"))
    if default is not None:
        out["default"] = default
    return out


def _agreed(field: str, values: List[Any], model_id: str, provider_specs: int) -> Optional[Any]:
    if not values:
        return None
    value = _single_distinct(values)
    if value is None:
        logger.debug("provider specs disagree on a model field; not attaching", model_id=model_id, field=field,
                     provider_specs=provider_specs, distinct_values=len({_canonical(v) for v in values}),
                     event_type="models_spec_field_disagree")
    return value


def spec_enrichment(model_id: str) -> Dict[str, Any]:
    """Fields for a GET /models row read from the provider-spec cache only (no ping).

    capabilities, model_family and thinking are each attached independently, when every cached provider spec
    that reports the field reports the same value.
    """
    specs = provider_api_spec_service.cached_specs_for_model(model_id)
    out: Dict[str, Any] = {}
    candidates = {
        "capabilities": [c for c in (_capabilities(s) for s in specs) if c is not None],
        "model_family": [f for f in (_bounded_str(s.get("modelFamily")) for s in specs) if f is not None],
        "thinking": [t for t in (_thinking(s) for s in specs) if t is not None],
    }
    for field, values in candidates.items():
        value = _agreed(field, values, model_id, len(specs))
        if value is not None:
            out[field] = value
    return out
