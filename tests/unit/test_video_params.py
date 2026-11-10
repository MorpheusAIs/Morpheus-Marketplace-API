"""OpenAI Videos ``seconds`` / ``size`` mapping, the response ``size``, and
the first-frame checks.

The resolution picked from ``size`` decides the clip price, so the
short-side thresholds are pinned at their boundaries. A first frame the
node would refuse is refused before the hold and the session.
"""

import base64
import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/test_db",
)

import pytest

from src.services.video.errors import VideoParamError
from datetime import datetime, timedelta

from src.services.video.params import (
    estimate_progress,
    parse_seconds,
    parse_size,
    parse_video_request,
    render_seconds_per_clip_second,
    video_size,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("8", 8),
        (" 12 ", 12),
        (4, 4),
        ("1", 1),
        (600, 600),
        ("0", VideoParamError),
        (0, VideoParamError),
        ("601", VideoParamError),
        ("4.5", VideoParamError),
        (4.0, VideoParamError),
        ("-3", VideoParamError),
        ("", VideoParamError),
        ("ten", VideoParamError),
        (True, VideoParamError),
        (None, VideoParamError),
    ],
)
def test_seconds_mapping(value, expected):
    if expected is VideoParamError:
        with pytest.raises(VideoParamError) as exc:
            parse_seconds(value)
        assert exc.value.status_code == 400
    else:
        assert parse_seconds(value) == expected


@pytest.mark.parametrize(
    "size, expected",
    [
        ("1280x720", ("720p", "16:9")),
        ("720x1280", ("720p", "9:16")),
        ("1792x1024", ("1080p", "16:9")),
        ("1024x1792", ("1080p", "9:16")),
        ("1920X1080", ("1080p", "16:9")),
        ("640x480", ("480p", "4:3")),
        ("480x640", ("480p", "3:4")),
        ("2560x1080", ("1080p", "21:9")),
        # Short-side thresholds: <= 540 is 480p, <= 900 is 720p
        ("540x540", ("480p", "1:1")),
        ("541x541", ("720p", "1:1")),
        ("1600x900", ("720p", "16:9")),
        ("1602x901", ("1080p", "16:9")),
        ("1280*720", VideoParamError),
        ("1280x", VideoParamError),
        ("0x720", VideoParamError),
        ("8193x720", VideoParamError),
        ("big", VideoParamError),
        ("", VideoParamError),
        (1280, VideoParamError),
    ],
)
def test_size_mapping(size, expected):
    if expected is VideoParamError:
        with pytest.raises(VideoParamError) as exc:
            parse_size(size)
        assert exc.value.status_code == 400
    else:
        assert parse_size(size) == expected


@pytest.mark.parametrize(
    "resolution, aspect_ratio, expected",
    [
        ("720p", "16:9", "1280x720"),
        ("720p", "9:16", "720x1280"),
        ("480p", "16:9", "854x480"),
        ("480p", "9:16", "480x854"),
        ("1080p", "16:9", "1920x1080"),
        ("1080p", "9:16", "1080x1920"),
        ("720p", "1:1", "720x720"),
        ("480p", "4:3", "640x480"),
        ("720p", "adaptive", "1280x720"),
        ("720p", None, "1280x720"),
        ("4k", "16:9", None),
    ],
)
def test_response_size(resolution, aspect_ratio, expected):
    assert video_size(resolution, aspect_ratio) == expected


def _b64(data):
    return base64.b64encode(data).decode()


PNG_B64 = _b64(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
# The node's Go http.DetectContentType: "RIFF", 4 size bytes, "WEBPVP"
WEBP_B64 = _b64(b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16)
RIFF_WEBP_NO_VP_B64 = _b64(b"RIFF\x24\x00\x00\x00WEBPALPH" + b"\x00" * 16)
RIFF_WAVE_B64 = _b64(b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 16)


@pytest.mark.parametrize(
    "field, value, stored",
    [
        ("image_b64", PNG_B64, "data:image/png;base64," + PNG_B64),
        ("image_b64", "data:image/png;base64," + PNG_B64, "data:image/png;base64," + PNG_B64),
        ("image_b64", WEBP_B64, "data:image/webp;base64," + WEBP_B64),
        ("image_b64", "data:image/webp;base64," + WEBP_B64, "data:image/webp;base64," + WEBP_B64),
        # The node would not see WebP in these
        ("image_b64", "data:image/webp;base64," + RIFF_WEBP_NO_VP_B64, None),
        ("image_b64", "data:image/webp;base64," + RIFF_WAVE_B64, None),
        ("image_b64", RIFF_WEBP_NO_VP_B64, None),
        # Valid base64, not an image
        ("image_b64", "AAAA", None),
        ("image_b64", "data:image/jpeg;base64," + PNG_B64, None),
        ("image_b64", "data:image/png," + PNG_B64, None),
        ("image_b64", "not base64!", None),
        ("image_url", "https://cdn.example.com/frame.png", "https://cdn.example.com/frame.png"),
        ("image_url", "http://cdn.example.com/frame.png", None),
        ("image_url", "https://127.0.0.1/frame.png", None),
        ("image_url", "https://localhost/frame.png", None),
        ("image_url", "https://cdn.example.com/" + "a" * 2048, None),
    ],
)
def test_first_frame_checks(field, value, stored):
    fields = {"model": "seedance-2.5", "prompt": "a cat", field: value}
    if stored is None:
        with pytest.raises(VideoParamError) as exc:
            parse_video_request(fields)
        assert exc.value.param == field
        return
    request = parse_video_request(fields)
    assert getattr(request, field) == stored


NOW = datetime(2026, 11, 10, 12, 0, 0)


# A running clip's progress is the elapsed share of its expected render time
# (past rate x clip seconds, or 30 s per clip second without history), from 1
# and never 100 until the node says the clip is done.
@pytest.mark.parametrize(
    "status, elapsed, duration, rate, expected",
    [
        ("queued", 60, 4, None, 0),
        ("failed", 60, 4, None, 0),
        ("completed", 0, 4, None, 100),
        ("in_progress", 0, 4, None, 1),
        ("in_progress", 60, 4, None, 50),
        ("in_progress", 60, 4, 60.0, 25),
        ("in_progress", 60, 8, None, 25),
        ("in_progress", 10_000, 4, None, 95),
    ],
)
def test_estimate_progress(status, elapsed, duration, rate, expected):
    created = NOW - timedelta(seconds=elapsed)
    assert estimate_progress(status, created, duration, rate, now=NOW) == expected


@pytest.mark.parametrize(
    "samples, expected",
    [
        ([], None),
        ([30.0, 40.0], None),
        ([30.0, 50.0, 40.0], 40.0),
        ([30.0, 40.0, 50.0, 60.0], 45.0),
        ([30.0, 0.0, None, 40.0, 50.0], 40.0),
    ],
)
def test_render_seconds_per_clip_second_needs_three_samples(samples, expected):
    assert render_seconds_per_clip_second(samples) == expected
