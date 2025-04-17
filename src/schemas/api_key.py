from typing import Optional
from pydantic import BaseModel, ConfigDict

class APIKeyCreate(BaseModel):
    """
    Schema for API key creation request.
    """
    name: Optional[str] = None

class APIKeyResponse(BaseModel):
    """
    Schema for API key response.
    """
    key: str
    key_prefix: str
    name: Optional[str] = None

class APIKeyDB(BaseModel):
    """
    Schema for API key in database response.
    """
    id: int
    key_prefix: str
    name: Optional[str] = None
    created_at: str
    is_active: bool

    # Configure Pydantic to work with SQLAlchemy
    model_config = ConfigDict(from_attributes=True) 