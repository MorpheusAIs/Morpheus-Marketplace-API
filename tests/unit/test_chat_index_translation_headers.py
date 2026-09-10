"""Translation header propagation onto the chat completion response.

Covers:
- TranslationResult.headers() sanitizes untrusted provider-report values
  (see request_translation.TranslationResult.headers, section A of the fix wave)
- index.py's non-streaming response helper forwards extra_headers onto the
  JSONResponse it returns, alongside X-Request-Id
- index.py's streaming response helper forwards extra_headers onto the
  StreamingResponse it returns, without needing to iterate the body
"""
import os
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import JSONResponse, StreamingResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.api.v1.chat import index as chat_index  # noqa: E402
from src.api.v1.chat import request_translation as rt  # noqa: E402


def test_translation_result_headers_sanitizes_untrusted_provider_values():
    result = rt.TranslationResult(
        stack="vllm\r\nX-Injected: 1é",
        applied={"reasoning.disable": "chat_template_kwargs.enable_thinking"},
        unsupported=["reasoning.budget"],
        touched=True,
    )
    headers = result.headers()
    assert headers["X-Morpheus-Provider-Stack"] == "vllmX-Injected1"
    assert headers["X-Morpheus-Translated"] == "reasoning.disable=chat_template_kwargs.enable_thinking"
    assert headers["X-Morpheus-Unsupported"] == "reasoning.budget"

    # an all-unsafe stack yields no X-Morpheus-Provider-Stack key at all
    assert "X-Morpheus-Provider-Stack" not in rt.TranslationResult(stack="日本語").headers()


async def test_non_streaming_handler_forwards_extra_headers():
    success = JSONResponse(content={"ok": True}, status_code=200)
    with patch.object(chat_index, "handle_non_streaming_request", new_callable=AsyncMock, return_value=success), \
         patch.object(chat_index, "_finalize_billing", new_callable=AsyncMock, return_value=None), \
         patch.object(chat_index, "_release_session", new_callable=AsyncMock):
        response = await chat_index._handle_non_streaming_request(
            chat_logger=MagicMock(),
            request_id="req-1",
            session_id="0xsess",
            body=b'{"messages": []}',
            requested_model="llama-3.3-70b",
            model_id="0x01",
            db_api_key=MagicMock(key_prefix="abc"),
            user=MagicMock(id=1),
            ledger_entry_id=uuid.uuid4(),
            extra_headers={"X-Morpheus-Provider-Stack": "vllm"},
        )

    assert isinstance(response, JSONResponse)
    assert response.headers["X-Morpheus-Provider-Stack"] == "vllm"
    assert response.headers["X-Request-Id"] == "req-1"


def _trivial_stream_generator_factory():
    async def gen():
        yield b"data: test\n\n"
    return gen


def test_streaming_handler_forwards_extra_headers_without_iterating_body():
    with patch.object(chat_index, "build_stream_generator", return_value=_trivial_stream_generator_factory):
        response = chat_index._handle_streaming_request(
            chat_logger=MagicMock(),
            request_id="req-2",
            session_id="0xsess",
            body=b'{"messages": []}',
            requested_model="llama-3.3-70b",
            model_id="0x01",
            db_api_key=MagicMock(key_prefix="abc"),
            user=MagicMock(id=1),
            ledger_entry_id=uuid.uuid4(),
            token_estimate=MagicMock(),
            extra_headers={"X-Morpheus-Provider-Stack": "vllm"},
        )

    assert isinstance(response, StreamingResponse)
    assert response.headers["X-Morpheus-Provider-Stack"] == "vllm"
