from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime


class DelegationBase(BaseModel):
    delegate_address: str
    signed_delegation_data: str # Storing as string/text initially
    expiry: Optional[datetime] = None
    is_active: bool = True

class DelegationCreate(DelegationBase):
    # Data comes from the frontend after user signs
    pass

class DelegationUpdate(BaseModel):
    # Primarily for activating/deactivating
    is_active: Optional[bool] = None

class DelegationRead(DelegationBase):
    id: int
    user_id: int
    created_at: datetime

    class Config:
        from_attributes = True 