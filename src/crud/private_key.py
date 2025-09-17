import json
import logging
import re
from typing import Optional, Dict, Tuple
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from ..db.models import UserPrivateKey
from ..core.key_vault import key_vault
from ..core.config import settings
from ..schemas import private_key as schemas

# Setup logging
logger = logging.getLogger(__name__)


def sanitize_private_key(private_key: str) -> str:
    """
    Ensure the private key is in the correct format for Ethereum.
    
    Args:
        private_key: The private key to sanitize
        
    Returns:
        A properly formatted private key
    """
    if not private_key:
        return private_key
        
    # Remove whitespace and newlines
    private_key = private_key.strip()
    
    # Remove "0x" prefix if present
    if private_key.startswith("0x"):
        private_key = private_key[2:]
        
    # Ensure it's a valid hex string
    if not all(c in "0123456789abcdefABCDEF" for c in private_key):
        logger.warning("Private key contains non-hex characters")
        # Filter out non-hex characters
        private_key = ''.join(c for c in private_key if c in "0123456789abcdefABCDEF")
    
    # Ethereum private keys should be 64 characters (32 bytes) in hex
    if len(private_key) < 64:
        logger.warning(f"Private key is too short ({len(private_key)} chars), padding with zeros")
        # Pad with leading zeros if too short
        private_key = private_key.zfill(64)
    elif len(private_key) > 64:
        logger.warning(f"Private key is too long ({len(private_key)} chars), truncating to 64 chars")
        # Truncate if too long
        private_key = private_key[:64]
        
    # Return with 0x prefix which is the standard for Ethereum
    return "0x" + private_key.lower()


async def get_user_private_key(db: AsyncSession, user_id: int) -> Optional[UserPrivateKey]:
    """
    Get a user's stored private key entry from the database.
    
    Args:
        db: Database session
        user_id: ID of the user
        
    Returns:
        UserPrivateKey object if found, None otherwise
    """
    result = await db.execute(
        select(UserPrivateKey).where(UserPrivateKey.user_id == user_id)
    )
    return result.scalars().first()


async def create_user_private_key(
    db: AsyncSession, user_id: int, private_key: str
) -> UserPrivateKey:
    """
    Create a new encrypted private key entry for a user.
    
    Args:
        db: Database session
        user_id: ID of the user
        private_key: Plaintext private key to encrypt and store
        
    Returns:
        Created UserPrivateKey object
    """
    # Check if user already has a private key
    existing_key = await get_user_private_key(db, user_id)
    if existing_key:
        # Delete existing key before creating a new one
        await db.delete(existing_key)
        await db.commit()
    
    # Encrypt the private key
    encrypted_key, metadata = key_vault.encrypt(private_key)
    
    # Create a new database entry
    now = datetime.utcnow()
    db_private_key = UserPrivateKey(
        user_id=user_id,
        encrypted_private_key=encrypted_key,
        encryption_metadata=metadata,
        created_at=now,
        updated_at=now
    )
    
    # Save to database
    db.add(db_private_key)
    await db.commit()
    await db.refresh(db_private_key)
    
    return db_private_key


async def delete_user_private_key(db: AsyncSession, user_id: int) -> bool:
    """
    Delete a user's private key from the database.
    
    Args:
        db: Database session
        user_id: ID of the user
        
    Returns:
        True if a key was deleted, False otherwise
    """
    db_private_key = await get_user_private_key(db, user_id)
    if not db_private_key:
        return False
    
    await db.delete(db_private_key)
    await db.commit()
    return True


async def get_decrypted_private_key(db: AsyncSession, user_id: int) -> Optional[str]:
    """
    Retrieve and decrypt a user's private key.
    
    Args:
        db: Database session
        user_id: ID of the user
        
    Returns:
        Decrypted private key as string if found, None otherwise
    """
    db_private_key = await get_user_private_key(db, user_id)
    if not db_private_key:
        return None
    
    # Decrypt the private key
    try:
        decrypted_key = key_vault.decrypt(
            db_private_key.encrypted_private_key,
            db_private_key.encryption_metadata
        )
        # Sanitize the private key to ensure it's in the correct format
        return sanitize_private_key(decrypted_key)
    except Exception as e:
        # Log the error (in a real application)
        logger.error(f"Error decrypting private key: {e}")
        return None


async def get_private_key_with_fallback(db: AsyncSession, user_id: int) -> Tuple[str, bool]:
    """
    Retrieve and decrypt a user's private key, with fallback to the environment variable if not found.
    
    Args:
        db: Database session
        user_id: ID of the user
        
    Returns:
        Tuple of (private_key, is_fallback) where:
        - private_key: The decrypted private key as string
        - is_fallback: Boolean indicating if the fallback key was used
    """
    # Try to get the user's private key
    user_private_key = await get_decrypted_private_key(db, user_id)
    
    # If user private key exists, return it
    if user_private_key:
        return user_private_key, False
    
    # Otherwise, use the fallback key
    if settings.FALLBACK_PRIVATE_KEY:
        logger.warning(f"Private key not set for user {user_id}, using fallback key (FOR DEBUGGING ONLY)")
        # Sanitize the fallback key to ensure it's in the correct format
        return sanitize_private_key(settings.FALLBACK_PRIVATE_KEY), True
    
    # No private key and no fallback
    return None, True


async def user_has_private_key(db: AsyncSession, user_id: int) -> bool:
    """
    Check if a user has a private key stored.
    
    Args:
        db: Database session
        user_id: ID of the user
        
    Returns:
        True if user has a private key, False otherwise
    """
    db_private_key = await get_user_private_key(db, user_id)
    return db_private_key is not None 