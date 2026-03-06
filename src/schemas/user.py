from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime

# Shared properties (DB-backed fields only; email lives in Cognito)
class UserBase(BaseModel):
    is_active: Optional[bool] = True

# Properties to return to client (email resolved from Cognito at request time)
class UserResponse(UserBase):
    id: int
    cognito_user_id: str
    email: Optional[str] = None
    name: Optional[str] = None
    age_verified: bool = False
    age_verified_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

# Age verification consent request
class AgeVerificationRequest(BaseModel):
    """Schema for age verification consent submission."""
    age_verified: bool = Field(..., description="User confirms they are 18 years or older")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "age_verified": True
            }
        }
    )

# Properties for user deletion response
class UserDeletionResponse(BaseModel):
    """Schema for user deletion response"""
    message: str
    deleted_data: dict
    cognito_deletion: dict  # Status of Cognito user deletion
    user_id: int
    deleted_at: datetime
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "User account successfully deleted",
                "deleted_data": {
                    "sessions": 2,
                    "api_keys": 3,
                    "private_key": True,
                    "automation_settings": True,
                    "delegations": 0
                },
                "cognito_deletion": {
                    "success": True,
                    "cognito_user_id": "abc123-def456-ghi789",
                    "message": "User successfully deleted from Cognito"
                },
                "user_id": 123,
                "deleted_at": "2024-01-01T12:00:00Z"
            }
        }
    ) 