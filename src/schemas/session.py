from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class SessionBase(BaseModel):
    model: Optional[str] = None
    
class SessionCreate(SessionBase):
    pass
    
class Session(SessionBase):
    id: str
    api_key_id: Optional[int] = None
    user_id: Optional[int] = None
    model: str
    type: str
    created_at: datetime
    expires_at: datetime
    is_active: bool
    
    class Config:
        orm_mode = True 