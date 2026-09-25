from typing import List, Optional, Dict, Any, Union, Literal
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from enum import Enum


class ModelPermission(BaseModel):
    """OpenAI model permission schema"""
    id: str
    object: str = "model_permission"
    created: int = Field(..., description="Unix timestamp (seconds)")
    allow_create_engine: bool
    allow_sampling: bool
    allow_logprobs: bool
    allow_search_indices: bool
    allow_view: bool
    allow_fine_tuning: bool
    organization: str
    group: Optional[str] = None
    is_blocking: bool


class Model(BaseModel):
    """OpenAI model schema"""
    id: str
    object: str = "model"
    created: int = Field(..., description="Unix timestamp (seconds)")
    owned_by: str
    permission: List[ModelPermission]
    root: str
    parent: Optional[str] = None


class ModelList(BaseModel):
    """OpenAI models list response schema"""
    object: str = "list"
    data: List[Model]


class ModelModalities(BaseModel):
    input: Optional[List[str]] = Field(None, description="Accepted input kinds: text, image, audio, video, pdf")
    output: Optional[List[str]] = Field(None, description="Produced output kinds: text, image, audio, embeddings")


class ModelCapabilities(BaseModel):
    """Provider-reported facts about the model as served; an absent field is unknown, never false."""
    model_config = ConfigDict(extra="allow")

    context: Optional[int] = Field(None, description="Served context window in tokens (prompt + completion)")
    contextTrained: Optional[int] = Field(None, description="Trained context window when the backend reports both")
    maxOutput: Optional[int] = Field(None, description="Maximum completion tokens")
    modalities: Optional[ModelModalities] = None
    tools: Optional[bool] = Field(None, description="Function/tool calling accepted")
    toolsParallel: Optional[bool] = None
    structured: Optional[List[str]] = Field(None, description="Subset of json_object, json_schema, grammar, regex, choice")
    reasoning: Optional[bool] = Field(None, description="The model can reason; control is in thinking")
    webSearch: Optional[bool] = Field(None, description="The backend can search the web for this model on request")
    embeddings: Optional[bool] = None
    promptCache: Optional[bool] = None
    quantization: Optional[str] = Field(None, description="Backend's own label (fp8, Q4_K_M, bf16, ...)")


class ModelThinking(BaseModel):
    mode: str = Field(..., description="always_on | controllable | tunable")
    default: Optional[str] = Field(None, description="on, off or an effort level name; absent when unknown")


class MarketplaceModel(BaseModel):
    """A marketplace model row; capabilities, model_family and thinking come from the provider-spec cache
    and are present only when every cached provider spec for the model agrees."""
    id: str
    blockchainID: str
    created: int = Field(..., description="Unix timestamp (seconds)")
    tags: List[str]
    modelType: str
    capabilities: Optional[ModelCapabilities] = None
    model_family: Optional[str] = None
    thinking: Optional[ModelThinking] = None


class MarketplaceModelList(BaseModel):
    object: str = "list"
    data: List[MarketplaceModel]


class ChatErrorDetail(BaseModel):
    """OpenAI-style error body; the code is repeated in the X-Morpheus-Error response header."""
    message: str
    type: str = Field(..., description="e.g. invalid_request_error, model_routing_error")
    code: str = Field(..., description="e.g. context_length_exceeded, model_routing_error")
    param: Optional[str] = Field(None, description="The offending request field, e.g. messages")
    context_window: Optional[int] = Field(None, description="context_length_exceeded: the provider's window in tokens")
    estimated_prompt_tokens: Optional[int] = Field(None, description="context_length_exceeded: the gateway's prompt estimate")


class ChatErrorResponse(BaseModel):
    error: ChatErrorDetail


class ChatMessageRole(str, Enum):
    """Roles in a chat conversation"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    FUNCTION = "function"
    TOOL = "tool"


class ChatMessage(BaseModel):
    """Single message in a chat conversation"""
    role: ChatMessageRole
    content: str
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """OpenAI chat completion request schema.

    Provider-specific extras (e.g. venice_parameters) are allowed for parity
    with the live chat endpoint schema in api.v1.chat.chat_models.
    """
    model_config = ConfigDict(extra="allow")

    model: Optional[str] = None
    messages: List[ChatMessage]
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = True
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    logit_bias: Optional[Dict[str, float]] = None
    user: Optional[str] = None
    session_id: Optional[str] = None


class ChatCompletionChoice(BaseModel):
    """Single choice in a chat completion response"""
    index: int
    message: ChatMessage
    finish_reason: Optional[str] = None


class ChatCompletionResponseUsage(BaseModel):
    """Token usage information"""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    """OpenAI chat completion response schema"""
    id: str
    object: str = "chat.completion"
    created: int = Field(..., description="Unix timestamp (seconds)")
    model: str
    choices: List[ChatCompletionChoice]
    usage: ChatCompletionResponseUsage


# Streaming response schemas
class ChatCompletionChunkDelta(BaseModel):
    """Delta content for streaming responses"""
    role: Optional[ChatMessageRole] = None
    content: Optional[str] = None


class ChatCompletionChunkChoice(BaseModel):
    """Choice in a streaming chunk response"""
    index: int
    delta: ChatCompletionChunkDelta
    finish_reason: Optional[str] = None


class ChatCompletionChunk(BaseModel):
    """OpenAI streaming chat completion chunk schema"""
    id: str
    object: str = "chat.completion.chunk"
    created: int = Field(..., description="Unix timestamp (seconds)")
    model: str
    choices: List[ChatCompletionChunkChoice] 