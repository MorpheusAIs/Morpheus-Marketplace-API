import datetime
from typing import Optional, List
from datetime import timezone

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.core.security import generate_api_key, get_api_key_hash
from src.db.models import APIKey, User
from src.schemas.api_key import APIKeyCreate

async def get_api_key_by_id(db: AsyncSession, api_key_id: int) -> Optional[APIKey]:
    """
    Get an API key by ID.
    
    Args:
        db: Database session
        api_key_id: API key ID
        
    Returns:
        APIKey object if found, None otherwise
    """
    result = await db.execute(
        select(APIKey)
        .options(joinedload(APIKey.user))
        .where(APIKey.id == api_key_id)
    )
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
    result = await db.execute(
        select(APIKey)
        .options(joinedload(APIKey.user))
        .where(APIKey.key_prefix == key_prefix)
    )
    return result.scalars().first()

async def create_api_key(db: AsyncSession, user_id: int, api_key_in: APIKeyCreate) -> tuple[APIKey, str]:
    """
    Create a new API key for a user.
    
    Args:
        db: Database session
        user_id: User ID
        api_key_in: API key creation data
        
    Returns:
        Tuple of (APIKey object, plain text API key)
    """

    # Get user info for encryption
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise ValueError(f"User {user_id} not found")

    # Generate a new API key
    full_key, key_prefix = generate_api_key()
    
    # Hash the API key
    hashed_key = get_api_key_hash(full_key)

    # Encrypt the API key using Cognito user data
    encrypted_key = APIKeyEncryption.encrypt_api_key(
        full_key, user.cognito_user_id, user.email
    )
    
    # Create API key object with required fields
    api_key_data = {
        "key_prefix": key_prefix,
        "hashed_key": hashed_key,
        "user_id": user_id,
        "is_active": True,
        "encrypted_key": encrypted_key,
        "encryption_version": 1,
    }
    
    # Add optional name field if provided
    if api_key_in.name is not None:
        api_key_data["name"] = api_key_in.name
    
    # Create the API key object
    db_api_key = APIKey(**api_key_data)
    
    # Add to database
    db.add(db_api_key)
    await db.commit()
    await db.refresh(db_api_key)
    
    return db_api_key, full_key

async def get_user_api_keys(db: AsyncSession, user_id: int) -> List[APIKey]:
    """
    Get all API keys for a user.
    
    Args:
        db: Database session
        user_id: User ID
        
    Returns:
        List of APIKey objects
    """
    result = await db.execute(select(APIKey).where(APIKey.user_id == user_id))
    return result.scalars().all()

async def deactivate_api_key(db: AsyncSession, api_key_id: int, user_id: Optional[int] = None) -> Optional[APIKey]:
    """
    Deactivate an API key.
    
    Args:
        db: Database session
        api_key_id: API key ID
        user_id: Optional user ID to ensure ownership
        
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
    # Get current UTC time as timezone-aware datetime
    now_with_tz = datetime.datetime.now(timezone.utc)
    
    # Convert to naive datetime (remove timezone info) for DB compatibility
    naive_datetime = now_with_tz.replace(tzinfo=None)
    
    # Update the last_used_at timestamp
    api_key.last_used_at = naive_datetime
    await db.commit()
    
    return api_key

async def delete_all_user_api_keys(db: AsyncSession, user_id: int) -> int:
    """
    Delete all API keys for a user and return count of deleted keys.
    
    Args:
        db: Database session
        user_id: User ID
        
    Returns:
        Count of deleted API keys
    """
    # Get count of API keys to delete
    count_result = await db.execute(
        select(APIKey).where(APIKey.user_id == user_id)
    )
    api_keys = count_result.scalars().all()
    count = len(api_keys)
    
    # Delete all API keys for the user
    await db.execute(
        delete(APIKey).where(APIKey.user_id == user_id)
    )
    await db.commit()
    
    return count 

async def get_default_api_key(db: AsyncSession, user_id: int) -> Optional[APIKey]:
    """
    Get the user's default API key. If no default is set, returns the first (oldest) active API key.
    
    Args:
        db: Database session
        user_id: User ID
        
    Returns:
        Default APIKey object if found, None otherwise
    """
    # First, try to get the user-defined default key
    result = await db.execute(
        select(APIKey)
        .options(joinedload(APIKey.user))
        .where(APIKey.user_id == user_id)
        .where(APIKey.is_active == True)
        .where(APIKey.is_default == True)
        .limit(1)
    )
    default_key = result.scalars().first()
    
    if default_key:
        return default_key
    
    # If no default is set, fall back to the first (oldest) active key
    result = await db.execute(
        select(APIKey)
        .options(joinedload(APIKey.user))
        .where(APIKey.user_id == user_id)
        .where(APIKey.is_active == True)
        .order_by(APIKey.created_at.asc())
        .limit(1)
    )
    return result.scalars().first()

async def get_first_active_api_key(db: AsyncSession, user_id: int) -> Optional[APIKey]:
    """
    Get the first (oldest) active API key for a user.
    
    Args:
        db: Database session
        user_id: User ID
        
    Returns:
        First active APIKey object if found, None otherwise
    """
    result = await db.execute(
        select(APIKey)
        .options(joinedload(APIKey.user))
        .where(APIKey.user_id == user_id)
        .where(APIKey.is_active == True)
        .order_by(APIKey.created_at.asc())
        .limit(1)
    )
    return result.scalars().first()

async def set_default_api_key(db: AsyncSession, api_key_id: int, user_id: int) -> Optional[APIKey]:
    """
    Set an API key as the user's default. Clears any existing default.
    
    Args:
        db: Database session
        api_key_id: API key ID to set as default
        user_id: User ID for security check
        
    Returns:
        Updated APIKey object if successful, None otherwise
    """
    # First, clear any existing default for this user
    await db.execute(
        update(APIKey)
        .where(APIKey.user_id == user_id)
        .where(APIKey.is_default == True)
        .values(is_default=False)
    )
    
    # Set the new default
    result = await db.execute(
        update(APIKey)
        .where(APIKey.id == api_key_id)
        .where(APIKey.user_id == user_id)
        .where(APIKey.is_active == True)
        .values(is_default=True)
        .returning(APIKey)
    )
    
    updated_key = result.scalars().first()
    if updated_key:
        await db.commit()
        await db.refresh(updated_key)
        return updated_key
    
    return None

async def get_decrypted_api_key(db: AsyncSession, api_key_id: int, user_id: int) -> Optional[str]:
    """
    Get the decrypted full API key for a user.
    
    Args:
        db: Database session
        api_key_id: API key ID
        user_id: User ID (for security)
        
    Returns:
        Decrypted API key or None if not found/decryption fails
    """
    # Get API key with user info
    result = await db.execute(
        select(APIKey)
        .options(joinedload(APIKey.user))
        .where(APIKey.id == api_key_id, APIKey.user_id == user_id, APIKey.is_active == True)
    )
    api_key = result.scalar_one_or_none()
    
    if not api_key or not api_key.encrypted_key:
        return None
    
    # Decrypt using user's Cognito data
    decrypted_key = APIKeyEncryption.decrypt_api_key(
        api_key.encrypted_key,
        api_key.user.cognito_user_id,
        api_key.user.email
    )
    
    return decrypted_key

async def get_decrypted_default_api_key(db: AsyncSession, user_id: int) -> Optional[tuple[APIKey, str]]:
    """
    Get the user's default API key with decrypted full key.
    
    Args:
        db: Database session
        user_id: User ID
        
    Returns:
        Tuple of (APIKey object, decrypted full key) or None if not found
    """
    # Get default API key or first active key
    default_api_key = await get_default_api_key(db, user_id)
    
    if not default_api_key:
        return None
    
    # Decrypt the full key
    decrypted_key = await get_decrypted_api_key(db, default_api_key.id, user_id)
    
    if not decrypted_key:
        return None
    
    return default_api_key, decrypted_key 