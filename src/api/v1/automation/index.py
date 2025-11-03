from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from ....db.database import get_db, get_db_session
from ....dependencies import get_api_key_user, oauth2_scheme
from ....db.models import User
from ....crud import automation as automation_crud
from ....core.config import settings as app_settings

router = APIRouter(tags=["Automation"])

# Define the automation settings model
class AutomationSettingsBase(BaseModel):
    is_enabled: Optional[bool] = True
    session_duration: Optional[int] = 3600

    class Config:
        json_schema_extra = {
            "example": {
                "is_enabled": True,
                "session_duration": 3600
            }
        }

class AutomationSettings(AutomationSettingsBase):
    user_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        # Add json serialization for datetime
        json_encoders = {
            datetime: lambda dt: dt.isoformat()
        }


@router.get("/settings", response_model=AutomationSettings)
async def get_automation_settings(
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_api_key_user)
):
    """
    Get automation settings for the authenticated user.
    """
    # Check if user exists
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if system-wide feature flag is enabled
    if not app_settings.AUTOMATION_FEATURE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Automation feature is currently disabled system-wide"
        )
    
    # Get automation settings
    user_settings = await automation_crud.get_automation_settings(db, user.id)
    
    # If settings don't exist, create default settings
    if not user_settings:
        user_settings = await automation_crud.create_automation_settings(db, user.id)
    
    return user_settings


@router.put("/settings", response_model=AutomationSettings)
async def update_automation_settings(
    automation_settings: AutomationSettingsBase,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_api_key_user)
):
    """
    Update automation settings for the authenticated user.
    """
    # Check if user exists
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if system-wide feature flag is enabled
    if not app_settings.AUTOMATION_FEATURE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Automation feature is currently disabled system-wide"
        )
    
    # Validate session duration if provided
    if automation_settings.session_duration is not None:
        if automation_settings.session_duration < 60:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session duration must be at least 60 seconds"
            )
        if automation_settings.session_duration > 86400:  # 24 hours
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session duration cannot exceed 86400 seconds (24 hours)"
            )
    
    # Update automation settings
    updated_settings = await automation_crud.update_automation_settings(
        db,
        user.id,
        is_enabled=automation_settings.is_enabled,
        session_duration=automation_settings.session_duration
    )
    
    return updated_settings 