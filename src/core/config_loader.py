"""
Environment-aware JSON config loader for model pricing and rate limits.

Resolves the ENVIRONMENT variable to select the appropriate JSON files
from the models/ directory at the project root.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict

_MODELS_DIR = Path(__file__).resolve().parents[2] / "models"

_ENV_PREFIX_MAP: Dict[str, str] = {
    "production": "prod",
    "prod": "prod",
    "prd": "prod",
    "staging": "prod",
    "stg": "prod",
    "stage": "prod",
    "development": "test",
    "dev": "test",
    "test": "test",
    "tst": "test",
}

_cache: Dict[str, Any] = {}


def _get_prefix() -> str:
    env = os.getenv("ENVIRONMENT", "development").lower().strip()
    return _ENV_PREFIX_MAP.get(env, "test")


def _load_json(filename: str) -> Dict[str, Any]:
    if filename in _cache:
        return _cache[filename]

    filepath = _MODELS_DIR / filename
    with open(filepath, "r") as f:
        data = json.load(f)

    _cache[filename] = data
    return data


def load_model_prices() -> Dict[str, Any]:
    """Load the environment-appropriate model pricing config."""
    prefix = _get_prefix()
    return _load_json(f"{prefix}_model_price.json")


def load_rate_limits() -> Dict[str, Any]:
    """Load the environment-appropriate rate limit config."""
    prefix = _get_prefix()
    return _load_json(f"{prefix}_rate_limit.json")


def clear_cache() -> None:
    """Clear cached config data (useful for testing or hot-reload)."""
    _cache.clear()
