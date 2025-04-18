from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class PrivateKeyBase(BaseModel):
    """Base schema for private key operations"""
    pass


class PrivateKeyCreate(PrivateKeyBase):
    """Schema for creating a private key entry"""
    private_key: str = Field(..., description="User's blockchain private key")


class PrivateKeyStatus(BaseModel):
    """Schema for private key status response"""
    has_key: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PrivateKeyInDB(PrivateKeyBase):
    """Schema for representing a stored private key (for internal use)"""
    id: int
    user_id: int
    encrypted_private_key: bytes
    encryption_metadata: dict
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True 