from typing import Optional, List, Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.schemas.user import UserCreate, UserUpdate
from src.core.logging_config import get_auth_logger

logger = get_auth_logger()

async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    """
    Get a user by ID.
    
    Args:
        db: Database session
        user_id: User ID
        
    Returns:
        User object if found, None otherwise
    """
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalars().first()

async def get_user_by_cognito_id(db: AsyncSession, cognito_user_id: str) -> Optional[User]:
    """
    Get a user by Cognito user ID.
    
    Args:
        db: Database session
        cognito_user_id: Cognito user ID (sub claim)
        
    Returns:
        User object if found, None otherwise
    """
    result = await db.execute(select(User).where(User.cognito_user_id == cognito_user_id))
    return result.scalars().first()

async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    """
    Get a user by email.
    
    Args:
        db: Database session
        email: User email
        
    Returns:
        User object if found, None otherwise
    """
    result = await db.execute(select(User).where(User.email == email))
    return result.scalars().first()

async def create_user_from_cognito(db: AsyncSession, user_data: dict) -> User:
    """
    Create a new user from Cognito authentication data.
    
    Args:
        db: Database session
        user_data: User data from Cognito token
        
    Returns:
        Created user object
    """
    # Create user object
    db_user = User(
        cognito_user_id=user_data['cognito_user_id'],
        email=user_data['email'],
        name=user_data.get('name'),
        is_active=user_data.get('is_active', True)
    )
    
    # Add to database
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    
    logger.info("User created from Cognito authentication",
               user_id=db_user.id,
               cognito_user_id=user_data['cognito_user_id'],
               email=user_data['email'],
               event_type="user_created_from_cognito")
    
    return db_user

async def update_user(
    db: AsyncSession, *, db_user: User, user_in: Union[UserUpdate, dict]
) -> User:
    """
    Update a user.
    
    Args:
        db: Database session
        db_user: User object to update
        user_in: User update data
        
    Returns:
        Updated user object
    """
    # Convert to dict if not already
    update_data = user_in if isinstance(user_in, dict) else user_in.model_dump(exclude_unset=True)
    
    # Update user fields (exclude cognito_user_id and id which shouldn't be updated)
    for field, value in update_data.items():
        if hasattr(db_user, field) and field not in ["id", "cognito_user_id"]:
            setattr(db_user, field, value)
    
    # Commit changes
    await db.commit()
    await db.refresh(db_user)
    
    logger.info("User updated successfully",
               user_id=db_user.id,
               updated_fields=list(update_data.keys()),
               event_type="user_updated")
    
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
               email=user.email,
               event_type="user_deletion")
    
    # Delete user
    await db.delete(user)
    await db.commit()
    
    logger.info("User deleted successfully",
               user_id=user_id,
               event_type="user_deleted")
    
    return user

async def update_user_from_cognito(
    db: AsyncSession, *, db_user: User, cognito_service
) -> Optional[User]:
    """
    Update user data by fetching fresh information from Cognito.
    
    Args:
        db: Database session
        db_user: User object to update
        cognito_service: Cognito service instance
        
    Returns:
        Updated user object or None if Cognito fetch fails
    """
    try:
        logger.debug("Fetching user info from Cognito",
                    user_id=db_user.id,
                    cognito_user_id=db_user.cognito_user_id,
                    event_type="cognito_user_info_fetch_start")
        
        # Fetch user info from Cognito
        cognito_info = await cognito_service.get_user_info(db_user.cognito_user_id)
        
        if not cognito_info:
            logger.warning("No user info received from Cognito",
                          user_id=db_user.id,
                          cognito_user_id=db_user.cognito_user_id,
                          event_type="cognito_user_info_not_found")
            return None
        
        # Extract attributes from Cognito response
        attributes = cognito_info.get('attributes', {})
        email = attributes.get('email')
        
        # Prepare update data
        update_data = {}
        
        # Update email if we have a real email from Cognito
        if email and email != db_user.cognito_user_id:
            update_data['email'] = email
            update_data['name'] = email  # Use email as name since no name fields are collected
        
        # Apply updates if we have any
        if update_data:
            logger.info("Updating user with Cognito data",
                       user_id=db_user.id,
                       update_fields=list(update_data.keys()),
                       event_type="cognito_user_data_update")
            return await update_user(db, db_user=db_user, user_in=update_data)
        
        logger.debug("No updates needed from Cognito",
                    user_id=db_user.id,
                    event_type="cognito_no_updates_needed")
        return db_user
        
    except Exception as e:
        # Log error but don't fail - return the original user
        logger.error("Failed to update user from Cognito",
                    user_id=db_user.id,
                    cognito_user_id=db_user.cognito_user_id,
                    error=str(e),
                    event_type="cognito_user_update_error")
        return db_user 