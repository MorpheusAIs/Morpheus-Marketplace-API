"""
Pure mappings between the OpenAI Videos API and the node's video job fields.

- parse_seconds / parse_size: OpenAI ``seconds`` and ``size`` to the node's
  duration_seconds, resolution and aspect_ratio.
- video_size: the response ``size`` from a resolution and an aspect ratio.
- image_data_uri / image_b64_data_uri: an uploaded first frame, or a
  Morpheus image_b64, to the node's image_b64 (the same checks for both).
- parse_video_request: a POST /videos body (JSON or form fields) to a VideoRequest.
- to_video_object: a video_jobs row to the public Video object.

Nothing here touches the DB or the network. Bad input raises VideoParamError.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from src.db.models import VideoJobStatus
from .errors import VideoParamError

MAX_PROMPT_BYTES = 4000
MIN_SECONDS = 1
MAX_SECONDS = 600
MAX_SIZE_SIDE = 8192
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# Base64 of MAX_IMAGE_BYTES plus room for a "data:image/webp;base64," prefix
MAX_IMAGE_B64_CHARS = 4 * math.ceil(MAX_IMAGE_BYTES / 3) + 64
MAX_IMAGE_URL_CHARS = 2048
# Fits video_jobs.resolution / aspect_ratio (String(16))
MAX_ENUM_CHARS = 16

# Aspect ratios a size is snapped to, in tie-break order
SIZE_ASPECT_RATIOS: Tuple[Tuple[str, int, int], ...] = (
    ("16:9", 16, 9),
    ("9:16", 9, 16),
    ("4:3", 4, 3),
    ("3:4", 3, 4),
    ("1:1", 1, 1),
    ("21:9", 21, 9),
)
_RESOLUTION_SHORT_SIDES = {"480p": 480, "720p": 720, "1080p": 1080}

_SIZE_RE = re.compile(r"(\d{1,5})[xX](\d{1,5})")
_SECONDS_RE = re.compile(r"\d{1,4}")
_RESOLUTION_RE = re.compile(r"(\d{2,4})p")
_RATIO_RE = re.compile(r"(\d{1,3}):(\d{1,3})")

# Declared content type -> data URI subtype. image/jpg is a common mislabel of JPEG.
_IMAGE_TYPES = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
}

# Host suffixes that are local by convention (the node refuses them too)
_LOCAL_HOST_SUFFIXES = (".localhost", ".localdomain", ".local", ".internal", ".home.arpa")

_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off"})

# Row status -> public status. pending is a row not yet on the node.
PUBLIC_STATUS = {
    VideoJobStatus.pending.value: "queued",
    VideoJobStatus.queued.value: "queued",
    VideoJobStatus.in_progress.value: "in_progress",
    VideoJobStatus.completed.value: "completed",
    VideoJobStatus.failed.value: "failed",
}
# The node reports no render progress, so a running clip's progress is
# estimated from the time it has taken against how long this model's clips
# took at this resolution, per second of clip.
DEFAULT_RENDER_SECONDS_PER_CLIP_SECOND = 30.0
# A running clip stays below 100 until the node says it is done.
MAX_RUNNING_PROGRESS = 95
# Fewer finished clips than this give no estimate of their own.
MIN_RENDER_SAMPLES = 3


def render_seconds_per_clip_second(samples) -> Optional[float]:
    """Median render seconds per clip second, or None without enough samples."""
    values = sorted(s for s in samples if s is not None and s > 0)
    if len(values) < MIN_RENDER_SAMPLES:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def estimate_progress(status: str, created_at: Optional[datetime], duration_seconds: int,
                      seconds_per_clip_second: Optional[float],
                      now: Optional[datetime] = None) -> int:
    """
    Approximate percentage for a public status: 0 queued or failed, 100
    completed, and for in_progress the elapsed share of the expected render
    time, from 1 up to MAX_RUNNING_PROGRESS.
    """
    if status == "completed":
        return 100
    if status != "in_progress" or created_at is None:
        return 0
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    expected = (seconds_per_clip_second or DEFAULT_RENDER_SECONDS_PER_CLIP_SECOND) * max(1, duration_seconds or 1)
    elapsed = max(0.0, (now - created_at).total_seconds())
    return max(1, min(MAX_RUNNING_PROGRESS, int(elapsed * 100 / expected)))


def parse_seconds(value: Any, param: str = "seconds") -> int:
    """An int or an integer string, 1..600."""
    if isinstance(value, bool):
        raise VideoParamError(f"{param} must be a whole number of seconds", param)
    if isinstance(value, str):
        text = value.strip()
        if not _SECONDS_RE.fullmatch(text):
            raise VideoParamError(f"{param} must be a whole number of seconds", param)
        value = int(text)
    if not isinstance(value, int):
        raise VideoParamError(f"{param} must be a whole number of seconds", param)
    if not MIN_SECONDS <= value <= MAX_SECONDS:
        raise VideoParamError(f"{param} must be between {MIN_SECONDS} and {MAX_SECONDS}", param)
    return value


def parse_size(value: Any, param: str = "size") -> Tuple[str, str]:
    """``"WxH"`` to (resolution, aspect_ratio).

    The resolution comes from the short side (<= 540 -> 480p, <= 900 -> 720p,
    else 1080p). The aspect ratio is the nearest of SIZE_ASPECT_RATIOS on a
    log scale, so a size and its portrait twin always map to mirrored ratios.
    """
    if not isinstance(value, str):
        raise VideoParamError(f"{param} must be a string like 1280x720", param)
    match = _SIZE_RE.fullmatch(value.strip())
    if not match:
        raise VideoParamError(f"{param} must be a string like 1280x720", param)
    width, height = int(match.group(1)), int(match.group(2))
    if not (1 <= width <= MAX_SIZE_SIDE and 1 <= height <= MAX_SIZE_SIDE):
        raise VideoParamError(f"{param} sides must be between 1 and {MAX_SIZE_SIDE}", param)

    short = min(width, height)
    if short <= 540:
        resolution = "480p"
    elif short <= 900:
        resolution = "720p"
    else:
        resolution = "1080p"

    log_ratio = math.log(width / height)
    aspect_ratio = min(
        SIZE_ASPECT_RATIOS,
        key=lambda r: abs(log_ratio - math.log(r[1] / r[2])),
    )[0]
    return resolution, aspect_ratio


def video_size(resolution: Optional[str], aspect_ratio: Optional[str]) -> Optional[str]:
    """The ``"WxH"`` of a clip: short side from the resolution, long side
    short x ratio rounded to an even number. adaptive (or no ratio) uses 16:9.
    None when the resolution has no pixel height."""
    res = (resolution or "").strip().lower()
    short = _RESOLUTION_SHORT_SIDES.get(res)
    if short is None:
        match = _RESOLUTION_RE.fullmatch(res)
        if not match:
            return None
        short = int(match.group(1))

    ratio_w, ratio_h = 16, 9
    match = _RATIO_RE.fullmatch((aspect_ratio or "").strip())
    if match and int(match.group(1)) > 0 and int(match.group(2)) > 0:
        ratio_w, ratio_h = int(match.group(1)), int(match.group(2))

    wide, narrow = max(ratio_w, ratio_h), min(ratio_w, ratio_h)
    half = Fraction(short * wide, narrow) / 2
    long_side = 2 * math.floor(half + Fraction(1, 2))
    if ratio_w >= ratio_h:
        return f"{long_side}x{short}"
    return f"{short}x{long_side}"


def image_data_uri(content_type: Optional[str], data: bytes, param: str = "input_reference") -> str:
    """A JPEG, PNG or WebP upload (content type and magic bytes must agree,
    at most 10 MiB) as ``data:image/<type>;base64,...``."""
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    subtype = _IMAGE_TYPES.get(declared)
    if subtype is None:
        raise VideoParamError(f"{param} must be a JPEG, PNG or WebP image", param)
    if not data:
        raise VideoParamError(f"{param} is empty", param)
    if len(data) > MAX_IMAGE_BYTES:
        raise VideoParamError(f"{param} is larger than {MAX_IMAGE_BYTES} bytes", param)
    if _sniff_image(data) != subtype:
        raise VideoParamError(f"{param} content does not match its {declared} content type", param)
    return f"data:image/{subtype};base64,{base64.b64encode(data).decode('ascii')}"


def image_b64_data_uri(value: Any, param: str = "image_b64") -> str:
    """A Morpheus image_b64 (plain base64, or a ``data:<type>;base64,`` URI)
    checked as an upload is, and returned as a data URI. A plain value takes
    its type from the bytes."""
    if not isinstance(value, str) or not value.strip():
        raise VideoParamError(f"{param} must be a base64 string or a data URI", param)
    text = value.strip()
    if len(text) > MAX_IMAGE_B64_CHARS:
        raise VideoParamError(f"{param} is larger than {MAX_IMAGE_BYTES} bytes", param)
    declared = None
    if text[:5].lower() == "data:":
        head, comma, text = text[5:].partition(",")
        if not comma or not head.lower().endswith(";base64"):
            raise VideoParamError(f"{param} data URI must be base64", param)
        declared = head.split(";", 1)[0]
    try:
        # Line breaks are allowed, as the node's decoder allows them
        data = base64.b64decode(text.replace("\r", "").replace("\n", ""), validate=True)
    except (binascii.Error, ValueError):
        raise VideoParamError(f"{param} is not valid base64", param)
    if declared is None:
        sniffed = _sniff_image(data)
        declared = f"image/{sniffed}" if sniffed else None
    return image_data_uri(declared, data, param)


def _sniff_image(data: bytes) -> Optional[str]:
    """The image type as the node's Go http.DetectContentType sniffs it."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    # "RIFF", 4 size bytes, "WEBPVP": a RIFF without a VP8 chunk is not WebP to the node
    if data[:4] == b"RIFF" and data[8:14] == b"WEBPVP":
        return "webp"
    return None


def parse_bool(value: Any, param: str) -> bool:
    """A JSON bool, or a form word (true/false, 1/0, yes/no, on/off)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    raise VideoParamError(f"{param} must be true or false", param)


@dataclass(frozen=True)
class VideoRequest:
    """A validated POST /videos body. None fields take the model guide's defaults."""

    model: str
    prompt: str
    duration_seconds: Optional[int] = None
    resolution: Optional[str] = None
    aspect_ratio: Optional[str] = None
    generate_audio: Optional[bool] = None
    image_url: Optional[str] = None
    # A data URI of up to 10 MiB: kept out of repr so it never reaches a log
    image_b64: Optional[str] = field(default=None, repr=False)

    def node_request(self) -> Dict[str, Any]:
        """The node's job fields (POST v1/video/quote and v1/video/jobs)."""
        return {
            "prompt": self.prompt,
            "duration_seconds": self.duration_seconds,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "generate_audio": self.generate_audio,
            "image_url": self.image_url,
            "image_b64": self.image_b64,
        }


def _optional(fields: Mapping[str, Any], name: str) -> Any:
    """A field's value, with an empty form string treated as absent."""
    value = fields.get(name)
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _enum_value(value: Any, param: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VideoParamError(f"{param} must be a non-empty string", param)
    text = value.strip()
    if len(text) > MAX_ENUM_CHARS:
        raise VideoParamError(f"{param} is longer than {MAX_ENUM_CHARS} characters", param)
    return text


def _image_url(value: Any, param: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VideoParamError(f"{param} must be an https URL", param)
    text = value.strip()
    if len(text) > MAX_IMAGE_URL_CHARS or not _is_public_https_url(text):
        raise VideoParamError(
            f"{param} must be a public https URL of at most {MAX_IMAGE_URL_CHARS} characters", param
        )
    return text


def _is_public_https_url(text: str) -> bool:
    """The node's check of a first-frame URL (videojob.ValidateRemoteURL), so
    a URL it would refuse is refused before a session opens: https, a host,
    no credentials, and a public address or a public-looking name."""
    try:
        parts = urlsplit(text)
        host = parts.hostname
    except ValueError:
        return False
    if parts.scheme != "https" or not host or parts.username is not None or parts.password is not None:
        return False
    host = host.removesuffix(".")
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    if not host.isascii() or "." not in host or host in ("localhost", "localhost.localdomain"):
        return False
    if host.endswith(_LOCAL_HOST_SUFFIXES):
        return False
    labels = host.split(".")
    # All-numeric labels spell an IPv4 shorthand (127.1, 0x7f.1)
    return all(labels) and not all(_numeric_label(label) for label in labels)


def _numeric_label(label: str) -> bool:
    """A decimal, octal or hex number, as an IPv4 shorthand label is written."""
    if label.startswith("0x"):
        return all(c in "0123456789abcdef" for c in label[2:])
    return label.isdigit()


def parse_video_request(
    fields: Mapping[str, Any],
    *,
    reference_image: Optional[str] = None,
) -> VideoRequest:
    """
    Validate a POST /videos body and map it onto the node's job fields.

    Args:
        fields: The JSON object, or the form fields without the uploaded file.
        reference_image: The data URI of an uploaded ``input_reference`` file
            (see image_data_uri), when the body was multipart.

    OpenAI fields: prompt, model, seconds, size, input_reference (a file, or
    JSON ``{"image_url": ...}``). Morpheus fields win over them when both are
    given: duration_seconds, resolution, aspect_ratio, generate_audio,
    image_url, image_b64. Every given field is validated, winner or not.
    """
    model = fields.get("model")
    if model is None:
        model = ""
    if not isinstance(model, str):
        raise VideoParamError("model must be a string", "model")

    prompt = fields.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise VideoParamError("prompt is required", "prompt")
    prompt = prompt.strip()
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise VideoParamError(f"prompt is longer than {MAX_PROMPT_BYTES} bytes", "prompt")

    duration_seconds = None
    seconds = _optional(fields, "seconds")
    if seconds is not None:
        duration_seconds = parse_seconds(seconds, "seconds")
    morpheus_duration = _optional(fields, "duration_seconds")
    if morpheus_duration is not None:
        duration_seconds = parse_seconds(morpheus_duration, "duration_seconds")

    resolution = aspect_ratio = None
    size = _optional(fields, "size")
    if size is not None:
        resolution, aspect_ratio = parse_size(size, "size")
    morpheus_resolution = _optional(fields, "resolution")
    if morpheus_resolution is not None:
        resolution = _enum_value(morpheus_resolution, "resolution").lower()
    morpheus_aspect = _optional(fields, "aspect_ratio")
    if morpheus_aspect is not None:
        aspect_ratio = _enum_value(morpheus_aspect, "aspect_ratio")

    generate_audio = None
    audio = _optional(fields, "generate_audio")
    if audio is not None:
        generate_audio = parse_bool(audio, "generate_audio")

    image_url = None
    image_b64 = reference_image
    reference = fields.get("input_reference")
    if reference is not None:
        if reference_image is not None or not isinstance(reference, dict):
            raise VideoParamError(
                'input_reference must be an image file, or {"image_url": "https://..."} in JSON',
                "input_reference",
            )
        image_url = _image_url(reference.get("image_url"), "input_reference.image_url")

    morpheus_url = _optional(fields, "image_url")
    morpheus_b64 = _optional(fields, "image_b64")
    if morpheus_url is not None:
        morpheus_url = _image_url(morpheus_url, "image_url")
    if morpheus_b64 is not None:
        morpheus_b64 = image_b64_data_uri(morpheus_b64, "image_b64")
    if morpheus_url is not None or morpheus_b64 is not None:
        # The Morpheus image fields replace input_reference as a whole
        image_url, image_b64 = morpheus_url, morpheus_b64

    return VideoRequest(
        model=model.strip(),
        prompt=prompt,
        duration_seconds=duration_seconds,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        generate_audio=generate_audio,
        image_url=image_url,
        image_b64=image_b64,
    )


def parse_node_time(value: Any) -> Optional[datetime]:
    """A node RFC3339 timestamp as naive UTC (the video_jobs column type)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _unix(value: Optional[datetime]) -> Optional[int]:
    """Naive-UTC column value as a unix timestamp."""
    if value is None:
        return None
    return int(value.replace(tzinfo=timezone.utc).timestamp())


def to_video_object(job: Any, seconds_per_clip_second: Optional[float] = None) -> Dict[str, Any]:
    """
    The public Video object of a video_jobs row (OpenAI Videos shape plus
    the Morpheus resolution, aspect_ratio and generate_audio). Never carries
    video_url: the clip is fetched through /content. seconds_per_clip_second
    is the model's past render rate for the progress estimate.
    """
    status = PUBLIC_STATUS.get(job.status, "queued")
    error = None
    if status == "failed" and (job.error_code or job.error_message):
        error = {"code": job.error_code, "message": job.error_message}
    return {
        "id": job.id,
        "object": "video",
        "model": job.model_name,
        "status": status,
        "progress": estimate_progress(status, job.created_at, job.duration_seconds,
                                      seconds_per_clip_second),
        "created_at": _unix(job.created_at),
        "completed_at": _unix(job.completed_at),
        "expires_at": _unix(job.expires_at),
        "seconds": str(job.duration_seconds),
        "size": video_size(job.resolution, job.aspect_ratio),
        "prompt": job.prompt,
        "error": error,
        "remixed_from_video_id": None,
        "resolution": job.resolution,
        "aspect_ratio": job.aspect_ratio,
        "generate_audio": bool(job.generate_audio),
    }
