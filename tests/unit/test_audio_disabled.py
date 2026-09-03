"""Audio endpoints stay mounted but reject traffic until billing exists."""

import os
from unittest.mock import patch

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/test_db",
)

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.v1.audio.index import AUDIO_DISABLED_DETAIL, router as audio_router
from src.services import session_routing_service


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(audio_router)
    return TestClient(app)


def test_transcriptions_returns_410_gone():
    response = _client().post("/audio/transcriptions")
    assert response.status_code == 410
    assert response.json()["detail"] == AUDIO_DISABLED_DETAIL


def test_speech_returns_410_gone():
    response = _client().post("/audio/speech", json={"input": "hello"})
    assert response.status_code == 410
    assert response.json()["detail"] == AUDIO_DISABLED_DETAIL


def test_disabled_audio_does_not_open_a_session():
    with patch.object(
        session_routing_service, "route_request", autospec=True
    ) as route_request:
        _client().post("/audio/speech", json={"input": "hello"})
        route_request.assert_not_called()
