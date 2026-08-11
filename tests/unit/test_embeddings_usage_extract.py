"""Unit tests for embeddings usage extraction (billing finalize)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.api.v1.embeddings.usage import extract_embeddings_usage


def test_prefers_usage_from_provider():
    body = {
        "usage_from_provider": {"prompt_tokens": 12, "total_tokens": 12},
        "usage": {"prompt_tokens": 99, "total_tokens": 99},
    }
    assert extract_embeddings_usage(body) == (12, 12, "usage_from_provider")


def test_falls_back_to_openai_usage():
    """Node embeddings path returns OpenAI-style usage only (DJohnston gap)."""
    body = {
        "object": "list",
        "data": [{"embedding": [0.1], "index": 0}],
        "usage": {"prompt_tokens": 42, "total_tokens": 42},
    }
    assert extract_embeddings_usage(body) == (42, 42, "usage")


def test_empty_usage_from_provider_falls_back():
    body = {
        "usage_from_provider": {},
        "usage": {"prompt_tokens": 7, "total_tokens": 7},
    }
    assert extract_embeddings_usage(body) == (7, 7, "usage")


def test_prompt_tokens_only_fills_total():
    body = {"usage": {"prompt_tokens": 15}}
    assert extract_embeddings_usage(body) == (15, 15, "usage")


def test_missing_usage_returns_none():
    assert extract_embeddings_usage({"data": []}) == (0, 0, "none")
    assert extract_embeddings_usage({}) == (0, 0, "none")
    assert extract_embeddings_usage(None) == (0, 0, "none")
