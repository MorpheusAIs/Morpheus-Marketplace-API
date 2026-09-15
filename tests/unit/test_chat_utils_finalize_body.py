import json
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.api.v1.chat import chat_utils  # noqa: E402


def test_finalize_request_body_applies_normalizers_before_serializing():
    json_body = {
        "messages": [{"role": "user", "content": "price?"}],
        "tools": [{"type": "function", "function": {"name": "get_price", "parameters": {"type": "object", "properties": {}, "tool_choice": "auto"}}}],
    }
    body = chat_utils.finalize_request_body(json_body, MagicMock())
    wire = json.loads(body.decode("utf-8"))
    assert "tool_choice" not in wire["tools"][0]["function"]["parameters"], "normalizer output must reach the wire"
    assert wire["messages"] == [{"role": "user", "content": "price?"}]


def test_finalize_request_body_is_plain_json_bytes():
    body = chat_utils.finalize_request_body({"messages": [], "reasoning": {"enabled": False}}, MagicMock())
    assert json.loads(body) == {"messages": [], "reasoning": {"enabled": False}}
