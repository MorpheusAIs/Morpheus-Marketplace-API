from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """Represents a single chat message in the OpenAI-compatible schema."""

    role: str
    content: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


class ToolFunction(BaseModel):
    """Function definition for tool calling."""

    name: str
    description: Optional[str] = None
    parameters: Dict[str, Any] = {}


class Tool(BaseModel):
    """Tool wrapper for function-based tools (OpenAI-compatible)."""

    type: str = "function"
    function: ToolFunction


class ToolChoice(BaseModel):
    """Explicit tool selection for tool calling."""

    type: Optional[str] = "function"
    function: Optional[Dict[str, Any]] = None


class ChatCompletionRequest(BaseModel):
    """Request payload for chat completions.

    Note: Field defaults and names must remain unchanged for API parity.
    """

    model: Optional[str] = None
    messages: List[ChatMessage]
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    tools: Optional[List[Tool]] = None
    tool_choice: Optional[Union[str, ToolChoice]] = None
    session_id: Optional[str] = Field(
        None,
        description=(
            "Optional session ID to use for this request. If not provided, the system "
            "will use the session associated with the API key."
        ),
    )


__all__ = [
    "ChatMessage",
    "ToolFunction",
    "Tool",
    "ToolChoice",
    "ChatCompletionRequest",
]


