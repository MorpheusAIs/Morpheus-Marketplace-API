"""Tests for client-facing error message sanitization."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.utils.error_sanitizer import sanitize_error_message


def test_redacts_private_urls():
    raw = "upstream failed at https://proxy.internal:8082/v1/chat"
    out = sanitize_error_message(raw)
    assert "proxy.internal" not in out
    assert "[redacted-url]" in out


def test_keeps_nodedocs_offramp():
    raw = (
        "Model 'qwen3.6:27b' is not available on the hosted gateway. "
        "For the full marketplace catalog, run a self-custody node: "
        "https://nodedocs.mor.org/consumers/quickstart"
    )
    out = sanitize_error_message(raw)
    assert "https://nodedocs.mor.org/consumers/quickstart" in out
    assert "[redacted-url]" not in out


def test_keeps_apidocs():
    raw = "See https://apidocs.mor.org/docs for the hosted API"
    assert "https://apidocs.mor.org/docs" in sanitize_error_message(raw)


def test_keeps_active_mor_catalog_url():
    raw = (
        "We couldn't open a session — the lowest usable bid for this model "
        "is above the hosted gateway limit of ~1.07 MOR/hr "
        "(compare the MOR/hr column on https://active.mor.org). "
        "For higher volume or the full marketplace catalog, run a self-custody node: "
        "https://nodedocs.mor.org/consumers/quickstart"
    )
    out = sanitize_error_message(raw)
    assert "https://active.mor.org" in out
    assert "https://nodedocs.mor.org/consumers/quickstart" in out
    assert "[redacted-url]" not in out


def test_keeps_bare_nodedocs_host():
    raw = "Docs: nodedocs.mor.org/consumers/quickstart"
    out = sanitize_error_message(raw)
    assert "nodedocs.mor.org" in out
    assert "[redacted-host]" not in out


def test_still_redacts_tokens():
    raw = "Authorization: Bearer supersecret"
    out = sanitize_error_message(raw)
    assert "supersecret" not in out
    assert "[redacted" in out
