"""
Client-facing errors of the /v1/videos API.

VideoError renders the OpenAI-style ``{"error": {...}}`` body. It is not a
ChatError on purpose: the ChatError handler adds chat-only translation
headers. main.py registers its own handler for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse

from src.utils.error_sanitizer import sanitize_error_message


@dataclass
class VideoError(Exception):
    """An error answered as ``{"error": {"message", "type", "code"?, **details}}``."""

    status_code: int
    error_type: str
    message: str
    code: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        super().__init__(self.message)

    def to_response(self) -> JSONResponse:
        error: Dict[str, Any] = {
            "message": sanitize_error_message(self.message),
            "type": self.error_type,
        }
        if self.code is not None:
            error["code"] = self.code
        error.update(self.details)
        return JSONResponse(
            status_code=self.status_code,
            content={"error": error},
            headers=self.headers,
        )


class VideoParamError(VideoError):
    """A malformed POST /videos field: 400 invalid_request naming the field."""

    def __init__(self, message: str, param: Optional[str] = None):
        super().__init__(
            status_code=400,
            error_type="invalid_request",
            message=message,
            details={"param": param},
        )
        self.param = param


def video_disabled_error() -> VideoError:
    return VideoError(410, "video_disabled", "Video generation is disabled")


def video_not_found_error() -> VideoError:
    return VideoError(404, "not_found", "Video not found")
