from typing import Optional
from pydantic import BaseModel, ConfigDict
from datetime import datetime

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
    created_at: datetime
    is_active: bool
    is_default: bool = False
    encrypted_key: Optional[str] = None  # For internal use only (not exposed to frontend)
    encryption_version: int = 1

    # Configure Pydantic to work with SQLAlchemy
    model_config = ConfigDict(from_attributes=True) 