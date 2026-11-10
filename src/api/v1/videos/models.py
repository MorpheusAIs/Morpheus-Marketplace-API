"""
OpenAPI documentation models for /v1/videos (OpenAI Videos compatible).

The routes parse their bodies themselves (JSON or multipart) and return
plain dicts; these models only describe the shapes in the API docs.
"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class VideoCreateRequest(BaseModel):
    """POST /videos body. Morpheus fields win over the OpenAI ones when both are given."""

    prompt: str = Field(..., description="What the clip shows; at most 4000 bytes")
    model: str = Field(..., description="Video model name or hex id from GET /v1/models (no default)")
    seconds: Optional[Union[str, int]] = Field(None, description="Clip length in seconds, 1 to 600")
    size: Optional[str] = Field(
        None,
        description="WxH, e.g. 1280x720. Maps to resolution (short side) and the nearest aspect ratio",
    )
    input_reference: Optional[Dict[str, str]] = Field(
        None,
        description='First frame: an image file in multipart, or {"image_url": "https://..."} in JSON',
    )
    duration_seconds: Optional[int] = Field(None, description="Morpheus: clip length in seconds (wins over seconds)")
    resolution: Optional[str] = Field(None, description="Morpheus: 480p, 720p or 1080p (wins over size)")
    aspect_ratio: Optional[str] = Field(None, description="Morpheus: e.g. 16:9, 9:16, 1:1, adaptive (wins over size)")
    generate_audio: Optional[bool] = Field(None, description="Morpheus: generate a soundtrack (priced per second)")
    image_url: Optional[str] = Field(
        None, description="Morpheus: first frame as a public https URL of at most 2048 characters"
    )
    image_b64: Optional[str] = Field(
        None, description="Morpheus: first frame as base64 or a data URI (JPEG, PNG or WebP, at most 10 MiB)"
    )


def create_request_body() -> Dict[str, Any]:
    """openapi_extra of POST /videos: the Idempotency-Key header, and the same
    body fields as JSON or as multipart form fields."""
    json_schema = VideoCreateRequest.model_json_schema()
    form_schema = {
        **json_schema,
        "properties": {
            **json_schema["properties"],
            "input_reference": {
                "type": "string",
                "format": "binary",
                "description": "First frame: a JPEG, PNG or WebP file of at most 10 MiB",
            },
        },
    }
    return {
        "parameters": [{
            "name": "Idempotency-Key",
            "in": "header",
            "required": False,
            "schema": {"type": "string", "maxLength": 255},
            "description": "A retry with the same key answers with the first video and places no new hold",
        }],
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {"schema": form_schema},
                "application/json": {"schema": json_schema},
            },
        }
    }


class VideoErrorDetail(BaseModel):
    code: Optional[str] = Field(
        None,
        description="Node or vendor code, or a gateway code: session_open_failed, node_error, "
                    "timeout, node_lost, submit_interrupted",
    )
    message: Optional[str] = None


class Video(BaseModel):
    """A video job. progress is approximate: 0 queued or failed, 100 completed, and
    while in_progress the elapsed share of this model's past render time at this
    resolution (at most 95 until the node reports the clip done)."""

    id: str
    object: Literal["video"] = "video"
    model: str
    status: Literal["queued", "in_progress", "completed", "failed"]
    progress: int
    created_at: int = Field(..., description="Unix timestamp (seconds)")
    completed_at: Optional[int] = Field(None, description="Unix timestamp (seconds)")
    expires_at: Optional[int] = Field(None, description="When the download link stops working, when known")
    seconds: str
    size: Optional[str] = Field(None, description="WxH")
    prompt: str
    error: Optional[VideoErrorDetail] = None
    remixed_from_video_id: Optional[str] = None
    resolution: Optional[str] = None
    aspect_ratio: Optional[str] = None
    generate_audio: bool = False


class VideoList(BaseModel):
    object: Literal["list"] = "list"
    data: List[Video]
    first_id: Optional[str] = None
    last_id: Optional[str] = None
    has_more: bool
