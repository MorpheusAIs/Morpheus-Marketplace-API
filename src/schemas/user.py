from typing import Optional
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from datetime import datetime

# Shared properties
class UserBase(BaseModel):
    email: EmailStr
    name: Optional[str] = None
    is_active: Optional[bool] = True

# Properties to receive on user creation
class UserCreate(UserBase):
    password: str = Field(..., min_length=8)

# Properties to receive on user update
class UserUpdate(UserBase):
    password: Optional[str] = Field(None, min_length=8)

# Properties to return to client
class UserResponse(UserBase):
    id: int
    
    # Configure Pydantic to work with SQLAlchemy
    model_config = ConfigDict(from_attributes=True)

# Properties for authentication
class UserLogin(BaseModel):
    """Schema for user login credentials"""
    email: EmailStr = Field(..., description="Email address for login")
    password: str = Field(..., description="User password", min_length=8)
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "user@example.com",
                "password": "securepassword"
            }
        }
    )

# Properties for user deletion response
class UserDeletionResponse(BaseModel):
    """Schema for user deletion response"""
    message: str
    deleted_data: dict
    user_id: int
    deleted_at: datetime
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "User account successfully deleted",
                "deleted_data": {
                    "api_keys": 3,
                    "private_key": True,
                    "automation_settings": True,
                    "delegations": 0
                },
                "user_id": 123,
                "deleted_at": "2024-01-01T12:00:00Z"
            }
        }
    ) 