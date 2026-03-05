from typing import Optional, List, Union
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.services.cache_service import cache_service
from src.core.logging_config import get_auth_logger

logger = get_auth_logger()

async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalars().first()

async def get_user_by_cognito_id(db: AsyncSession, cognito_user_id: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.cognito_user_id == cognito_user_id))
    return result.scalars().first()

async def create_user_from_cognito(db: AsyncSession, cognito_user_id: str) -> User:
    """Create a new user row keyed by cognito_user_id (no PII stored)."""
    db_user = User(
        cognito_user_id=cognito_user_id,
        is_active=True,
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    
    logger.info("User created from Cognito authentication",
               user_id=db_user.id,
               cognito_user_id=cognito_user_id,
               event_type="user_created_from_cognito")
    
    return db_user

async def update_user(
    db: AsyncSession, *, db_user: User, user_in: dict
) -> User:
    update_data = user_in if isinstance(user_in, dict) else user_in.model_dump(exclude_unset=True)
    
    for field, value in update_data.items():
        if hasattr(db_user, field) and field not in ["id", "cognito_user_id"]:
            setattr(db_user, field, value)
    
    await db.commit()
    await db.refresh(db_user)
    
    logger.info("User updated successfully",
               user_id=db_user.id,
               updated_fields=list(update_data.keys()),
               event_type="user_updated")
    
    await cache_service.delete("user", db_user.cognito_user_id)
    
    return db_user

async def get_all_users(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[User]:
    """
    Get all users with pagination.
    
    Args:
        db: Database session
        skip: Number of records to skip
        limit: Maximum number of records to return
        
    Returns:
        List of user objects
    """
    result = await db.execute(select(User).offset(skip).limit(limit))
    return result.scalars().all()

async def delete_user(db: AsyncSession, user_id: int) -> Optional[User]:
    """
    Delete a user.
    
    Args:
        db: Database session
        user_id: User ID to delete
        
    Returns:
        Deleted user object or None if not found
    """
    # Get user by ID
    user = await get_user_by_id(db, user_id)
    
    # Return None if user not found
    if not user:
        logger.warning("User not found for deletion",
                      user_id=user_id,
                      event_type="user_not_found_for_deletion")
        return None
    
    logger.info("Deleting user",
               user_id=user_id,
               cognito_user_id=user.cognito_user_id,
               event_type="user_deletion")
    
    # Invalidate user cache
    await cache_service.delete("user", user.cognito_user_id)
    
    # Delete user
    await db.delete(user)
    await db.commit()
    
    logger.info("User deleted successfully",
               user_id=user_id,
               event_type="user_deleted")
    
    return user

async def set_age_verification(db: AsyncSession, user_id: int, verified: bool) -> Optional[User]:
    """
    Record the user's 18+ age verification consent.

    Args:
        db: Database session
        user_id: User ID
        verified: Whether the user confirmed they are 18+

    Returns:
        Updated user object, or None if user not found
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        logger.warning("User not found for age verification",
                      user_id=user_id,
                      event_type="age_verification_user_not_found")
        return None

    if user.age_verified and user.age_verified_at:
        logger.info("Age already verified, preserving original consent timestamp",
                    user_id=user.id,
                    age_verified_at=user.age_verified_at.isoformat(),
                    event_type="age_verification_already_done")
        return user

    user.age_verified = verified
    user.age_verified_at = datetime.utcnow()

    await db.commit()
    await db.refresh(user)

    logger.info("Age verification recorded",
               user_id=user.id,
               age_verified=verified,
               age_verified_at=user.age_verified_at.isoformat(),
               event_type="age_verification_updated")

    await cache_service.delete("user", user.cognito_user_id)

    return user