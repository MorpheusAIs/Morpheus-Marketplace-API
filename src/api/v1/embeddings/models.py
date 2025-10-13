from typing import List, Optional, Union
from pydantic import BaseModel
from src.schemas import openai as openai_schemas


class EmbeddingRequest(openai_schemas.BaseModel):
    """Request model for embeddings endpoint"""
    input: Union[str, List[str]]
    model: Optional[str] = None
    encoding_format: Optional[str] = "float"
    dimensions: Optional[int] = None
    user: Optional[str] = None
    session_id: Optional[str] = None

class EmbeddingObject(openai_schemas.BaseModel):
    """Individual embedding object"""
    object: str = "embedding"
    embedding: List[float]
    index: int

class EmbeddingUsage(openai_schemas.BaseModel):
    """Usage statistics for embedding request"""
    prompt_tokens: int
    total_tokens: int

class EmbeddingResponse(openai_schemas.BaseModel):
    """Response model for embeddings endpoint"""
    object: str = "list"
    data: List[EmbeddingObject]
    model: str
    usage: EmbeddingUsage