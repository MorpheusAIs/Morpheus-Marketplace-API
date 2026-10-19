"""The CORS lists the app registers: what browsers may send and read on chat/model calls."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi.testclient import TestClient  # noqa: E402

from src import main as main_module  # noqa: E402
from src.core.cors_middleware import CredentialSafeCORSMiddleware  # noqa: E402


def _cors_kwargs():
    found = [m.kwargs for m in main_module.app.user_middleware if m.cls is CredentialSafeCORSMiddleware]
    assert len(found) == 1
    return found[0]


def test_translation_opt_in_header_is_allowed_on_preflight():
    assert main_module.settings.REQUEST_TRANSLATION_HEADER in _cors_kwargs()["allow_headers"]
    assert main_module.settings.REQUEST_TRANSLATION_HEADER in main_module.CORS_ALLOW_HEADERS


def test_error_and_translation_headers_are_exposed():
    exposed = _cors_kwargs()["expose_headers"]
    for header in ("X-Morpheus-Error", "X-Morpheus-Context-Window", "X-Morpheus-Provider-Stack",
                   "X-Morpheus-Unlisted-Params", "X-Morpheus-Capability-Mismatch"):
        assert header in exposed, header


def test_preflight_advertises_the_registered_lists():
    client = TestClient(main_module.app, base_url="https://testserver")  # the app's HTTPS guard
    response = client.options(
        "/api/v1/chat/completions",
        headers={"Origin": "https://app.mor.org", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": main_module.settings.REQUEST_TRANSLATION_HEADER},
    )
    assert response.status_code == 204
    allowed = [h.strip() for h in response.headers["Access-Control-Allow-Headers"].split(",")]
    assert main_module.settings.REQUEST_TRANSLATION_HEADER in allowed
    exposed = [h.strip() for h in response.headers["Access-Control-Expose-Headers"].split(",")]
    assert "X-Morpheus-Error" in exposed


def test_cors_check_mirrors_the_registered_lists():
    client = TestClient(main_module.app, base_url="https://testserver")  # the app's HTTPS guard
    response = client.get("/cors-check")
    assert response.status_code == 200
    config = response.json()["cors_config"]
    assert config["allowed_headers"] == main_module.CORS_ALLOW_HEADERS
    assert config["exposed_headers"] == main_module.CORS_EXPOSE_HEADERS
