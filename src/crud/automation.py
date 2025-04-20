from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, delete
from typing import Optional

from ..db.models import UserAutomationSettings


async def create_automation_settings(
    db: AsyncSession,
    user_id: int,
    is_enabled: bool = False,
    session_duration: int = 3600
) -> UserAutomationSettings:
    """
    Create new automation settings for a user.

    Args:
        db: Database session
        user_id: ID of the user
        is_enabled: Whether automation is enabled
        session_duration: Default session duration in seconds

    Returns:
        The created UserAutomationSettings object
    """
    settings = UserAutomationSettings(
        user_id=user_id,
        is_enabled=is_enabled,
        session_duration=session_duration
    )
    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    return settings


async def get_automation_settings(
    db: AsyncSession, 
    user_id: int
) -> Optional[UserAutomationSettings]:
    """
    Get automation settings for a user.

    Args:
        db: Database session
        user_id: ID of the user

    Returns:
        The UserAutomationSettings object if found, None otherwise
    """
    result = await db.execute(
        select(UserAutomationSettings).where(
            UserAutomationSettings.user_id == user_id
        )
    )
    return result.scalars().first()


async def update_automation_settings(
    db: AsyncSession,
    user_id: int,
    is_enabled: Optional[bool] = None,
    session_duration: Optional[int] = None
) -> Optional[UserAutomationSettings]:
    """
    Update automation settings for a user.

    Args:
        db: Database session
        user_id: ID of the user
        is_enabled: Whether automation is enabled
        session_duration: Default session duration in seconds

    Returns:
        The updated UserAutomationSettings object if found, None otherwise
    """
    # First check if settings exist
    settings = await get_automation_settings(db, user_id)
    
    # If settings don't exist, create them
    if not settings:
        update_data = {}
        if is_enabled is not None:
            update_data["is_enabled"] = is_enabled
        if session_duration is not None:
            update_data["session_duration"] = session_duration
            
        return await create_automation_settings(
            db, 
            user_id,
            is_enabled=update_data.get("is_enabled", False),
            session_duration=update_data.get("session_duration", 3600)
        )
    
    # Build update dict with only provided values
    update_data = {}
    if is_enabled is not None:
        update_data["is_enabled"] = is_enabled
    if session_duration is not None:
        update_data["session_duration"] = session_duration
    
    # If no values provided, just return existing settings
    if not update_data:
        return settings
    
    # Apply the update
    await db.execute(
        update(UserAutomationSettings)
        .where(UserAutomationSettings.user_id == user_id)
        .values(**update_data)
    )
    await db.commit()
    
    # Fetch and return updated settings
    return await get_automation_settings(db, user_id)


async def delete_automation_settings(
    db: AsyncSession, 
    user_id: int
) -> bool:
    """
    Delete automation settings for a user.

    Args:
        db: Database session
        user_id: ID of the user

    Returns:
        True if settings were deleted, False otherwise
    """
    result = await db.execute(
        delete(UserAutomationSettings).where(
            UserAutomationSettings.user_id == user_id
        )
    )
    await db.commit()
    return result.rowcount > 0 