import datetime
from typing import Optional, List
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import generate_api_key, get_api_key_hash
from src.db.models import APIKey, User
from src.schemas.api_key import APIKeyCreate

async def get_api_key_by_id(db: AsyncSession, api_key_id: UUID) -> Optional[APIKey]:
    """
    Get an API key by ID.
    
    Args:
        db: Database session
        api_key_id: API key UUID
        
    Returns:
        APIKey object if found, None otherwise
    """
    result = await db.execute(select(APIKey).where(APIKey.id == api_key_id))
    return result.scalars().first()

async def get_api_key_by_prefix(db: AsyncSession, key_prefix: str) -> Optional[APIKey]:
    """
    Get an API key by prefix.
    
    Args:
        db: Database session
        key_prefix: API key prefix (e.g., "sk-abcdef")
        
    Returns:
        APIKey object if found, None otherwise
    """
    result = await db.execute(select(APIKey).where(APIKey.key_prefix == key_prefix))
    return result.scalars().first()

async def create_api_key(db: AsyncSession, user_id: UUID, api_key_in: APIKeyCreate) -> tuple[APIKey, str]:
    """
    Create a new API key for a user.
    
    Args:
        db: Database session
        user_id: User UUID
        api_key_in: API key creation data
        
    Returns:
        Tuple of (APIKey object, plain text API key)
    """
    # Generate a new API key
    full_key, key_prefix = generate_api_key()
    
    # Hash the API key
    hashed_key = get_api_key_hash(full_key)
    
    # Create API key object
    db_api_key = APIKey(
        key_prefix=key_prefix,
        hashed_key=hashed_key,
        user_id=user_id,
        name=api_key_in.name,
        is_active=True
    )
    
    # Add to database
    db.add(db_api_key)
    await db.commit()
    await db.refresh(db_api_key)
    
    return db_api_key, full_key

async def get_user_api_keys(db: AsyncSession, user_id: UUID) -> List[APIKey]:
    """
    Get all API keys for a user.
    
    Args:
        db: Database session
        user_id: User UUID
        
    Returns:
        List of APIKey objects
    """
    result = await db.execute(select(APIKey).where(APIKey.user_id == user_id))
    return result.scalars().all()

async def deactivate_api_key(db: AsyncSession, api_key_id: UUID, user_id: Optional[UUID] = None) -> Optional[APIKey]:
    """
    Deactivate an API key.
    
    Args:
        db: Database session
        api_key_id: API key UUID
        user_id: Optional user UUID to ensure ownership
        
    Returns:
        Deactivated APIKey object if found and owned by user, None otherwise
    """
    # Get API key
    query = select(APIKey).where(APIKey.id == api_key_id)
    
    # Add user filter if provided
    if user_id:
        query = query.where(APIKey.user_id == user_id)
    
    result = await db.execute(query)
    api_key = result.scalars().first()
    
    # Return None if API key not found or not owned by user
    if not api_key:
        return None
    
    # Deactivate API key
    api_key.is_active = False
    await db.commit()
    await db.refresh(api_key)
    
    return api_key

async def update_last_used(db: AsyncSession, api_key: APIKey) -> APIKey:
    """
    Update the last_used_at timestamp of an API key.
    
    Args:
        db: Database session
        api_key: APIKey object
        
    Returns:
        Updated APIKey object
    """
    # Update the last_used_at timestamp
    api_key.last_used_at = datetime.datetime.now(datetime.timezone.utc)
    await db.commit()
    
    return api_key 