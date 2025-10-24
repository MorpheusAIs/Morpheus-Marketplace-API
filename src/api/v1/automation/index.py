from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from ....db.database import get_db
from ....dependencies import get_current_user
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get automation settings for the authenticated user.
    
    Requires JWT Bearer authentication with Cognito token.
    Automation settings control automatic session creation behavior for all of the user's API keys.
    """
    # Check if user exists (should always be true after get_current_user)
    if not current_user:
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
    user_settings = await automation_crud.get_automation_settings(db, current_user.id)
    
    # If settings don't exist, create default settings
    if not user_settings:
        user_settings = await automation_crud.create_automation_settings(db, current_user.id)
    
    return user_settings


@router.put("/settings", response_model=AutomationSettings)
async def update_automation_settings(
    automation_settings: AutomationSettingsBase,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update automation settings for the authenticated user.
    
    Requires JWT Bearer authentication with Cognito token.
    Automation settings control automatic session creation behavior for all of the user's API keys.
    """
    # Check if user exists (should always be true after get_current_user)
    if not current_user:
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
        current_user.id,
        is_enabled=automation_settings.is_enabled,
        session_duration=automation_settings.session_duration
    )
    
    return updated_settings 