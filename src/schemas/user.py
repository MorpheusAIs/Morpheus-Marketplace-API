from typing import Optional
from pydantic import BaseModel, EmailStr, Field, ConfigDict

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