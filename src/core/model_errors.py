"""Model resolution errors raised by ModelRouter / DirectModelService.

These are intentional client-facing failures (wrong capability, near-miss name).
API layers convert them to OpenAI-compatible JSON responses so agents can
stop/alert instead of silently continuing on a substitute model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class ModelRoutingError(Exception):
    """Base class for hard model-resolution failures."""

    message: str
    error_type: str = "model_routing_error"
    code: str = "model_routing_error"
    status_code: int = 400
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        super().__init__(self.message)

    def to_error_body(self) -> dict:
        """OpenAI-compatible error object (plus machine-readable code/details)."""
        body: dict[str, Any] = {
            "message": self.message,
            "type": self.error_type,
            "code": self.code,
            **self.details,
        }
        return {"error": body}


@dataclass
class ModelTypeMismatchError(ModelRoutingError):
    """Requested model exists but is incompatible with the endpoint type."""

    requested_model: Optional[str] = None
    resolved_model: Optional[str] = None
    resolved_id: Optional[str] = None
    requested_type: Optional[str] = None
    model_type: Optional[str] = None
    message: str = "Model type is incompatible with this endpoint"
    error_type: str = "model_type_mismatch"
    code: str = "model_type_mismatch"
    status_code: int = 400

    def __post_init__(self):
        endpoint_hint = {
            "LLM": "use /v1/chat/completions with an LLM model",
            "EMBEDDINGS": "use /v1/embeddings with an embedding model",
        }.get(self.requested_type or "", "use the matching endpoint for this model type")

        self.message = (
            f"Model '{self.requested_model}' is type '{self.model_type or 'unknown'}' "
            f"and cannot be used for '{self.requested_type}' requests; {endpoint_hint}."
        )
        self.details = {
            "requested_model": self.requested_model,
            "resolved_model": self.resolved_model,
            "resolved_id": self.resolved_id,
            "requested_type": self.requested_type,
            "model_type": self.model_type,
            "code": self.code,
        }
        super().__post_init__()


@dataclass
class ModelNearMissError(ModelRoutingError):
    """Requested name is not in the catalog, but close matches exist."""

    requested_model: Optional[str] = None
    suggestions: List[str] = field(default_factory=list)
    message: str = "Model not found"
    error_type: str = "model_not_found"
    code: str = "model_not_found_near_miss"
    status_code: int = 400

    def __post_init__(self):
        suggestion_txt = ", ".join(f"'{s}'" for s in self.suggestions[:5]) or "(none)"
        self.message = (
            f"Model '{self.requested_model}' was not found in active models. "
            f"Did you mean: {suggestion_txt}?"
        )
        self.details = {
            "requested_model": self.requested_model,
            "suggestions": list(self.suggestions),
            "code": self.code,
        }
        super().__post_init__()
