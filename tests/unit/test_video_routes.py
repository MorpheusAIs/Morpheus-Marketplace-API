"""The /videos routes: ownership, when a clip may be downloaded, idempotent
POST retries, and client errors in the {"error": {...}} shape."""

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/test_db",
)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm.evaluator import _EvaluatorCompiler

from src.api.v1.videos import index as videos_index
from src.api.v1.videos.index import router as videos_router
from src.db.models import VideoJob
from src.dependencies import get_api_key_user, get_current_api_key
from src.services.rate_limiting import RateLimitResult, RateLimitStatus
from src.services.video import jobs
from src.services.video.errors import VideoError

OWNER, OTHER = 7, 8
VIDEO_URL = "https://vendor.example/clip.mp4?sig=bearer"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class FakeDb:
    """video_jobs in memory; WHERE clauses run through SQLAlchemy's ORM evaluator."""

    def __init__(self, *rows):
        self.rows = list(rows)

    @asynccontextmanager
    async def get_db(self):
        yield self

    async def execute(self, stmt):
        match = _EvaluatorCompiler(VideoJob).process(stmt.whereclause)
        return _Result([row for row in self.rows if match(row)])

    async def commit(self):
        pass


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _row(**overrides):
    now = _utcnow()
    values = dict(
        id=jobs.new_video_id(), user_id=OWNER, api_key_id=1,
        model_name="seedance-2.5", model_id="0x" + "5e" * 32, prompt="a cat on a skateboard",
        duration_seconds=10, resolution="1080p", aspect_ratio="16:9", generate_audio=True, has_image=False,
        status="completed", price_usd=Decimal("2.70000000"), ledger_entry_id=uuid.uuid4(),
        billing_state="charged", session_id="0x" + "5a" * 32, session_closed_at=now,
        node_job_id="vj_1", node_misses=0, video_url=VIDEO_URL,
        expires_at=now + timedelta(hours=1), completed_at=now,
        created_at=now - timedelta(minutes=5), updated_at=now,
    )
    values.update(overrides)
    return VideoJob(**values)


def _client(caller: int) -> TestClient:
    app = FastAPI()
    app.include_router(videos_router)
    app.add_exception_handler(VideoError, lambda request, exc: exc.to_response())
    app.dependency_overrides[get_api_key_user] = lambda: SimpleNamespace(id=caller, rate_limit_multiplier=1.0)
    app.dependency_overrides[get_current_api_key] = lambda: SimpleNamespace(id=1, key_prefix="sk-test")
    return TestClient(app, follow_redirects=False)


@pytest.mark.parametrize(
    "path, caller, expected_status",
    [
        ("/videos/{id}", OWNER, 200),
        ("/videos/{id}", OTHER, 404),
        ("/videos/{id}/content", OWNER, 307),
        ("/videos/{id}/content", OTHER, 404),
    ],
)
def test_only_the_owner_sees_a_video(path, caller, expected_status):
    row = _row()
    db = FakeDb(row)
    with patch.object(jobs, "get_db", db.get_db):
        response = _client(caller).get(path.format(id=row.id))

    assert response.status_code == expected_status
    if expected_status == 404:
        assert response.json()["error"]["type"] == "not_found"
    if expected_status != 307:
        # The bearer link only ever leaves through the owner's /content
        assert VIDEO_URL not in response.text
        assert "location" not in response.headers


@pytest.mark.parametrize(
    "row_values, query, expected_status, error_type",
    [
        # Still rendering on the node
        (dict(status="queued", billing_state="held", video_url=None, expires_at=None,
              completed_at=None, session_closed_at=None), "", 409, "video_not_ready"),
        (dict(status="failed", billing_state="voided", video_url=None, expires_at=None,
              completed_at=None, error_code="job_failed"), "", 409, "video_not_ready"),
        (dict(expires_at=_utcnow() - timedelta(minutes=1)), "?redirect=false", 410, "video_expired"),
        (dict(), "", 307, None),
        # For browsers: the vendor's storage has no CORS, so the link is played directly
        (dict(), "?redirect=false", 200, None),
        # A bad flag answers in the {error} shape, not FastAPI's 422
        (dict(), "?redirect=nope", 400, "invalid_request"),
    ],
)
def test_content_download(row_values, query, expected_status, error_type):
    row = _row(**row_values)
    db = FakeDb(row)
    with patch.object(jobs, "get_db", db.get_db), \
            patch("src.services.proxy_router_service.getVideoJob",
                  AsyncMock(return_value={"status": "running"})):
        response = _client(OWNER).get(f"/videos/{row.id}/content{query}")

    assert response.status_code == expected_status
    if error_type:
        assert response.json()["error"]["type"] == error_type
        assert VIDEO_URL not in response.text
        return
    if expected_status == 307:
        assert response.headers["location"] == VIDEO_URL
    else:
        assert "location" not in response.headers
        assert response.json()["url"] == VIDEO_URL
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize(
    "first_owner, replayed",
    [
        (OWNER, True),
        # A key is scoped to its user: another user's video never comes back
        (OTHER, False),
    ],
)
def test_post_retry_with_the_same_idempotency_key(first_owner, replayed):
    first = _row(user_id=first_owner, status="pending", idempotency_key="retry-1")
    created = _row(status="pending")
    submit = AsyncMock(return_value=created)
    with patch.object(jobs, "get_db", FakeDb(first).get_db), \
            patch.object(videos_index.video_jobs, "submit_video", submit), \
            patch.object(videos_index.rate_limit_service, "check_rate_limit",
                         AsyncMock(return_value=RateLimitResult(allowed=True, status=RateLimitStatus.ALLOWED))), \
            patch.object(videos_index.model_router, "get_target_model", AsyncMock(return_value=first.model_id)), \
            patch.object(videos_index.direct_model_service, "get_model_name_from_id",
                         AsyncMock(return_value="seedance-2.5")):
        response = _client(OWNER).post(
            "/videos",
            json={"model": "seedance-2.5", "prompt": "a cat on a skateboard"},
            headers={"Idempotency-Key": "retry-1"},
        )

    assert response.status_code == 200
    assert response.json()["id"] == (first.id if replayed else created.id)
    assert submit.await_count == (not replayed)


def _chunked(body: bytes):
    yield body[: len(body) // 2]
    yield body[len(body) // 2:]


@pytest.mark.parametrize(
    "method, path, kwargs, expected_status, error_type",
    [
        ("get", "/videos?limit=abc", {}, 400, "invalid_request"),
        ("get", "/videos?limit=0", {}, 400, "invalid_request"),
        ("get", "/videos?order=newest", {}, 400, "invalid_request"),
        ("post", "/videos", dict(content=b"x", headers={"content-type": "multipart/form-data"}),
         400, "invalid_request"),
        ("post", "/videos", dict(content=b"garbage", headers={"content-type": "multipart/form-data; boundary=b"}),
         400, "invalid_request"),
        ("post", "/videos", dict(json={"prompt": "p"}, headers={"Idempotency-Key": "k" * 256}),
         400, "invalid_request"),
        # The cap holds without a Content-Length (a chunked body)
        ("post", "/videos", dict(content=_chunked(b'{"prompt": "' + b"p" * 2048 + b'"}'),
                                 headers={"content-type": "application/json"}),
         413, "request_too_large"),
    ],
)
def test_client_errors_use_the_error_shape(method, path, kwargs, expected_status, error_type):
    with patch.object(videos_index, "MAX_BODY_BYTES", 1024):
        response = getattr(_client(OWNER), method)(path, **kwargs)

    assert response.status_code == expected_status
    assert response.json()["error"]["type"] == error_type
