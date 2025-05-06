from typing import List, Optional, Dict, Any, Union, Literal
from pydantic import BaseModel, Field
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
    """OpenAI chat completion request schema"""
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