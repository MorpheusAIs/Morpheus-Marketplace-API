from typing import Optional, List
from datetime import datetime, timedelta
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import UserSession, APIKey

async def get_session_by_api_key_id(db: AsyncSession, api_key_id: int) -> Optional[UserSession]:
    """
    Get a session associated with an API key.
    
    Args:
        db: Database session
        api_key_id: API key ID
        
    Returns:
        UserSession object if found, None otherwise
    """
    result = await db.execute(select(UserSession).where(
        UserSession.api_key_id == api_key_id,
        UserSession.is_active == True
    ))
    return result.scalars().first()

async def get_session_by_id(db: AsyncSession, session_id: int) -> Optional[UserSession]:
    """
    Get a session by ID.
    
    Args:
        db: Database session
        session_id: Session ID
        
    Returns:
        UserSession object if found, None otherwise
    """
    result = await db.execute(select(UserSession).where(UserSession.id == session_id))
    return result.scalars().first()

async def get_session_by_blockchain_id(db: AsyncSession, blockchain_session_id: str) -> Optional[UserSession]:
    """
    Get a session by blockchain session ID.
    
    Args:
        db: Database session
        blockchain_session_id: Blockchain session ID (hex)
        
    Returns:
        UserSession object if found, None otherwise
    """
    result = await db.execute(select(UserSession).where(
        UserSession.session_id == blockchain_session_id,
        UserSession.is_active == True
    ))
    return result.scalars().first()

async def create_session(
    db: AsyncSession, 
    api_key_id: int, 
    blockchain_session_id: str, 
    model_id: str,
    session_duration: int = 3600
) -> UserSession:
    """
    Create a new session for an API key.
    If a session already exists for this API key, it will be deactivated.
    
    Args:
        db: Database session
        api_key_id: API key ID
        blockchain_session_id: Blockchain session ID (hex)
        model_id: Model or bid ID used to create the session
        session_duration: Session duration in seconds (default: 1 hour)
        
    Returns:
        Created UserSession object
    """
    # Deactivate any existing sessions for this API key
    await db.execute(
        update(UserSession)
        .where(UserSession.api_key_id == api_key_id)
        .values(is_active=False)
    )
    
    # Calculate expiration time
    expires_at = datetime.utcnow() + timedelta(seconds=session_duration)
    
    # Create new session
    db_session = UserSession(
        api_key_id=api_key_id,
        session_id=blockchain_session_id,
        model_id=model_id,
        expires_at=expires_at,
        is_active=True
    )
    
    db.add(db_session)
    await db.commit()
    await db.refresh(db_session)
    
    return db_session

async def update_session_status(db: AsyncSession, session_id: int, is_active: bool) -> Optional[UserSession]:
    """
    Update session active status.
    
    Args:
        db: Database session
        session_id: Session ID
        is_active: New active status
        
    Returns:
        Updated UserSession object if found, None otherwise
    """
    # Update session
    await db.execute(
        update(UserSession)
        .where(UserSession.id == session_id)
        .values(is_active=is_active)
    )
    await db.commit()
    
    # Get updated session
    return await get_session_by_id(db, session_id)

async def delete_session(db: AsyncSession, session_id: int) -> bool:
    """
    Delete a session.
    
    Args:
        db: Database session
        session_id: Session ID
        
    Returns:
        True if session was deleted, False otherwise
    """
    result = await db.execute(delete(UserSession).where(UserSession.id == session_id))
    await db.commit()
    
    return result.rowcount > 0 