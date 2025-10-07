import datetime
from typing import Optional, List
from datetime import timezone

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.core.security import generate_api_key, get_api_key_hash
from src.core.encryption import APIKeyEncryption
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
    
    # Hash the API key (keep for backward compatibility)
    hashed_key = get_api_key_hash(full_key)

    # Encrypt the API key using Cognito user ID
    encrypted_key = APIKeyEncryption.encrypt_api_key(
        full_key, user.cognito_user_id
    )
    
    # Create API key object with required fields
    api_key_data = {
        "key_prefix": key_prefix,
        "hashed_key": hashed_key,
        "encrypted_key": encrypted_key,
        "encryption_version": 1,
        "user_id": user_id,
        "is_active": True,
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
    from src.core.logging_config import get_auth_logger
    logger = get_auth_logger()
    
    logger.debug("Getting decrypted API key", 
                api_key_id=api_key_id,
                user_id=user_id,
                event_type="get_decrypted_api_key_start")
    
    # Get API key with user info
    result = await db.execute(
        select(APIKey)
        .options(joinedload(APIKey.user))
        .where(APIKey.id == api_key_id, APIKey.user_id == user_id, APIKey.is_active == True)
    )
    api_key = result.scalar_one_or_none()
    
    if not api_key:
        logger.warning("API key not found or not active", 
                      api_key_id=api_key_id,
                      user_id=user_id,
                      event_type="api_key_not_found")
        return None
    
    if not api_key.encrypted_key:
        logger.warning("API key has no encrypted data", 
                      api_key_id=api_key_id,
                      user_id=user_id,
                      event_type="no_encrypted_data")
        return None
    
    logger.debug("API key found, attempting decryption", 
                api_key_id=api_key_id,
                user_id=user_id,
                cognito_user_id=api_key.user.cognito_user_id[:8] + "...",
                user_email=api_key.user.email,
                encrypted_key_length=len(api_key.encrypted_key),
                event_type="api_key_found")
    
    # Decrypt using user's Cognito ID
    decrypted_key = APIKeyEncryption.decrypt_api_key(
        api_key.encrypted_key,
        api_key.user.cognito_user_id
    )
    
    if decrypted_key:
        logger.debug("API key decryption successful", 
                    api_key_id=api_key_id,
                    user_id=user_id,
                    event_type="decryption_successful")
    else:
        logger.error("API key decryption failed", 
                    api_key_id=api_key_id,
                    user_id=user_id,
                    event_type="decryption_failed")
    
    return decrypted_key

async def get_decrypted_default_api_key(db: AsyncSession, user_id: int) -> tuple[Optional[APIKey], Optional[str], str]:
    """
    Get the user's default API key with decrypted full key.
    
    Args:
        db: Database session
        user_id: User ID
        
    Returns:
        Tuple of (APIKey object, decrypted full key, status_message)
        - If successful: (APIKey, decrypted_key, "success")
        - If no key found: (None, None, "no_key_found")
        - If decryption failed: (APIKey, None, "decryption_failed")
    """
    from src.core.logging_config import get_auth_logger
    logger = get_auth_logger()
    
    logger.debug("Getting decrypted default API key", 
                user_id=user_id,
                event_type="get_decrypted_default_start")
    
    # Get default API key or first active key
    default_api_key = await get_default_api_key(db, user_id)
    
    if not default_api_key:
        logger.warning("No default API key found for user", 
                      user_id=user_id,
                      event_type="no_default_key_found")
        return None, None, "no_key_found"
    
    logger.debug("Default API key found, attempting decryption", 
                user_id=user_id,
                api_key_id=default_api_key.id,
                key_prefix=default_api_key.key_prefix,
                event_type="default_key_found")
    
    # Decrypt the full key
    decrypted_key = await get_decrypted_api_key(db, default_api_key.id, user_id)
    
    if not decrypted_key:
        logger.error("API key decryption failed for default key", 
                    user_id=user_id,
                    api_key_id=default_api_key.id,
                    key_prefix=default_api_key.key_prefix,
                    event_type="default_key_decryption_failed")
        return default_api_key, None, "decryption_failed"
    
    logger.debug("Default API key decryption successful", 
                user_id=user_id,
                api_key_id=default_api_key.id,
                key_prefix=default_api_key.key_prefix,
                event_type="default_key_decryption_success")
    
    return default_api_key, decrypted_key, "success" 