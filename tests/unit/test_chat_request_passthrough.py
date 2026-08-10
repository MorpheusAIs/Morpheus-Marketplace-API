"""Ensure chat request schema forwards provider-specific extras."""

from pydantic import ValidationError
import pytest

from src.api.v1.chat.chat_models import ChatCompletionRequest


def test_venice_parameters_and_unknown_extras_survive_dump():
    raw = {
        "model": "Venice Uncensored 1.2",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "who are you?"},
        ],
        "temperature": 0.7,
        "max_tokens": 2000,
        "stream": False,
        "venice_parameters": {"enable_web_search": "on"},
        "stream_options": {"include_usage": True},
    }
    req = ChatCompletionRequest.model_validate(raw)
    dumped = req.model_dump(exclude_none=True)

    assert dumped["venice_parameters"] == {"enable_web_search": "on"}
    assert dumped["stream_options"] == {"include_usage": True}
    assert dumped["messages"][0]["role"] == "system"
    assert dumped["messages"][0]["content"] == "You are a helpful assistant."
    assert dumped["temperature"] == 0.7


def test_known_field_types_still_validated():
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": "not-an-int",
            }
        )


def test_missing_messages_still_rejected():
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate({"model": "x", "venice_parameters": {}})
