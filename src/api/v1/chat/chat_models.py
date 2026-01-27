from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class ImageUrl(BaseModel):
    """Image URL for vision content parts."""

    url: str
    detail: Optional[str] = None  # "auto", "low", or "high"


class InputAudio(BaseModel):
    """Audio data for input_audio content parts."""

    data: str  # Base64-encoded audio data
    format: str  # "wav" or "mp3"


class FileData(BaseModel):
    """File reference for file content parts."""

    file_id: Optional[str] = None  # ID of uploaded file
    filename: Optional[str] = None  # Filename when using file_data
    file_data: Optional[str] = None  # Base64-encoded file data


class ContentPartText(BaseModel):
    """Text content part for multimodal messages."""

    type: str = "text"
    text: str


class ContentPartImageUrl(BaseModel):
    """Image URL content part for multimodal messages."""

    type: str = "image_url"
    image_url: ImageUrl


class ContentPartInputAudio(BaseModel):
    """Audio content part for multimodal messages."""

    type: str = "input_audio"
    input_audio: InputAudio


class ContentPartFile(BaseModel):
    """File content part for multimodal messages."""

    type: str = "file"
    file: FileData


# Union of all supported content part types
ContentPart = Union[
    ContentPartText,
    ContentPartImageUrl,
    ContentPartInputAudio,
    ContentPartFile,
]


class ChatMessage(BaseModel):
    """Represents a single chat message in the OpenAI-compatible schema.

    Content can be either a string or a list of content parts (for multimodal inputs).
    """

    role: str
    content: Optional[Union[str, List[ContentPart]]] = None
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
    "ImageUrl",
    "InputAudio",
    "FileData",
    "ContentPartText",
    "ContentPartImageUrl",
    "ContentPartInputAudio",
    "ContentPartFile",
    "ContentPart",
    "ChatMessage",
    "ToolFunction",
    "Tool",
    "ToolChoice",
    "ChatCompletionRequest",
]


