import json
from typing import Optional, Dict, Tuple
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from ..db.models import UserPrivateKey
from ..core.key_vault import key_vault
from ..schemas import private_key as schemas


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
        return decrypted_key
    except Exception as e:
        # Log the error (in a real application)
        print(f"Error decrypting private key: {e}")
        return None 