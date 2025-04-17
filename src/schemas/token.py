from typing import Optional
from uuid import UUID
from pydantic import BaseModel

class Token(BaseModel):
    """
    Schema for token response.
    """
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class TokenPayload(BaseModel):
    """
    Schema for token payload.
    """
    sub: Optional[str] = None
    type: str

class TokenRefresh(BaseModel):
    """
    Schema for token refresh request.
    """
    refresh_token: str

class APIKeyResponse(BaseModel):
    """
    Schema for API key response.
    """
    key: str
    key_prefix: str
    name: Optional[str] = None

class APIKeyCreate(BaseModel):
    """
    Schema for API key creation request.
    """
    name: Optional[str] = None

class APIKeyDB(BaseModel):
    """
    Schema for API key in database response.
    """
    id: UUID
    key_prefix: str
    name: Optional[str] = None
    created_at: str
    is_active: bool

    # Configure Pydantic to work with SQLAlchemy
    class Config:
        from_attributes = True 