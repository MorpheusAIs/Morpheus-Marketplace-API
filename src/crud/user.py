from typing import Optional, List, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import get_password_hash, verify_password
from src.db.models import User
from src.schemas.user import UserCreate, UserUpdate

async def get_user_by_id(db: AsyncSession, user_id: UUID) -> Optional[User]:
    """
    Get a user by ID.
    
    Args:
        db: Database session
        user_id: User UUID
        
    Returns:
        User object if found, None otherwise
    """
    result = await db.execute(select(User).where(User.id == user_id))
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

async def create_user(db: AsyncSession, user_in: UserCreate) -> User:
    """
    Create a new user.
    
    Args:
        db: Database session
        user_in: User creation data
        
    Returns:
        Created user object
    """
    # Hash the password
    hashed_password = get_password_hash(user_in.password)
    
    # Create user object
    db_user = User(
        email=user_in.email,
        name=user_in.name,
        hashed_password=hashed_password,
        is_active=True
    )
    
    # Add to database
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    
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
    
    # Hash the password if provided
    if "password" in update_data and update_data["password"]:
        update_data["hashed_password"] = get_password_hash(update_data.pop("password"))
    
    # Update user fields
    for field, value in update_data.items():
        if hasattr(db_user, field) and field != "id":
            setattr(db_user, field, value)
    
    # Commit changes
    await db.commit()
    await db.refresh(db_user)
    
    return db_user

async def authenticate_user(db: AsyncSession, email: str, password: str) -> Optional[User]:
    """
    Authenticate a user by email and password.
    
    Args:
        db: Database session
        email: User email
        password: User password
        
    Returns:
        User object if authenticated, None otherwise
    """
    # Get user by email
    user = await get_user_by_email(db, email)
    
    # Return None if user not found or inactive
    if not user or not user.is_active:
        return None
    
    # Verify password
    if not verify_password(password, user.hashed_password):
        return None
    
    return user

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

async def delete_user(db: AsyncSession, user_id: UUID) -> Optional[User]:
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
        return None
    
    # Delete user
    await db.delete(user)
    await db.commit()
    
    return user 