"""Hosted-gateway session routing policy (JSON from SESSION_ROUTING_POLICY_JSON).

Open-native catalog with:
  - aliases / preferences (steer)
  - deny names + ids (block)
  - max_stake_mor (per-open fuse on the bid we would open)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .config import settings
from .logging_config import get_models_logger

logger = get_models_logger()


@dataclass(frozen=True)
class SessionRoutingPolicy:
    aliases: Dict[str, str] = field(default_factory=dict)
    preferences: Dict[str, str] = field(default_factory=dict)
    deny_names: Set[str] = field(default_factory=set)
    deny_ids: Set[str] = field(default_factory=set)
    max_stake_mor: float = 0.0

    def alias_target(self, requested: str) -> Optional[str]:
        if not requested:
            return None
        return self.aliases.get(requested.strip().lower())

    def preference_target(self, requested: str) -> Optional[str]:
        if not requested:
            return None
        return self.preferences.get(requested.strip().lower())

    def is_denied_name(self, name: Optional[str]) -> bool:
        if not name:
            return False
        key = name.strip().lower()
        return key in self.deny_names

    def is_denied_id(self, model_id: Optional[str]) -> bool:
        if not model_id:
            return False
        return model_id.strip() in self.deny_ids


_EMPTY = SessionRoutingPolicy()
_cached_raw: Optional[str] = None
_cached_policy: SessionRoutingPolicy = _EMPTY


def _as_str_dict(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in value.items():
        if k is None or v is None:
            continue
        key = str(k).strip().lower()
        val = str(v).strip()
        if key and val:
            out[key] = val
    return out


def _as_str_set(value: Any) -> Set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
        return {p for p in parts if p}
    if isinstance(value, (list, tuple, set)):
        return {str(x).strip() for x in value if str(x).strip()}
    return set()


def parse_session_routing_policy(raw: Optional[str]) -> SessionRoutingPolicy:
    """Parse SESSION_ROUTING_POLICY_JSON. Invalid/empty → empty policy."""
    text = (raw or "").strip()
    if not text:
        return _EMPTY
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(
            "SESSION_ROUTING_POLICY_JSON is invalid JSON; ignoring policy",
            error=str(e),
            event_type="session_routing_policy_parse_error",
        )
        return _EMPTY
    if not isinstance(data, dict):
        logger.error(
            "SESSION_ROUTING_POLICY_JSON must be an object; ignoring policy",
            event_type="session_routing_policy_parse_error",
        )
        return _EMPTY

    deny = data.get("deny") or {}
    if not isinstance(deny, dict):
        deny = {}

    deny_names = {n.strip().lower() for n in _as_str_set(deny.get("names"))}
    deny_ids = {i.strip() for i in _as_str_set(deny.get("ids"))}

    try:
        max_stake = float(data.get("max_stake_mor") or 0)
    except (TypeError, ValueError):
        max_stake = 0.0

    return SessionRoutingPolicy(
        aliases=_as_str_dict(data.get("aliases")),
        preferences=_as_str_dict(data.get("preferences")),
        deny_names=deny_names,
        deny_ids=deny_ids,
        max_stake_mor=max(0.0, max_stake),
    )


def get_session_routing_policy() -> SessionRoutingPolicy:
    """Return the current policy (re-parses when env string changes)."""
    global _cached_raw, _cached_policy
    raw = getattr(settings, "SESSION_ROUTING_POLICY_JSON", "") or ""
    if raw != _cached_raw:
        _cached_policy = parse_session_routing_policy(raw)
        _cached_raw = raw
        logger.info(
            "Loaded session routing policy",
            alias_count=len(_cached_policy.aliases),
            preference_count=len(_cached_policy.preferences),
            deny_name_count=len(_cached_policy.deny_names),
            deny_id_count=len(_cached_policy.deny_ids),
            max_stake_mor=_cached_policy.max_stake_mor,
            event_type="session_routing_policy_loaded",
        )
    return _cached_policy


def reset_session_routing_policy_cache() -> None:
    """Test helper."""
    global _cached_raw, _cached_policy
    _cached_raw = None
    _cached_policy = _EMPTY
