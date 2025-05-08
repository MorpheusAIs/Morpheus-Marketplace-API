import asyncio
import logging
import httpx
import os
import json
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
import base64

from ..db.models import Session
from ..core.config import settings
from ..crud import session as session_crud
from ..crud import private_key as private_key_crud
from .proxy_router import execute_proxy_router_operation
from ..core.model_routing import model_router

logger = logging.getLogger(__name__)

async def create_automated_session(
    db: AsyncSession = None,
    api_key_id: Optional[int] = None, 
    user_id: Optional[int] = None,
    requested_model: Optional[str] = None,
    session_duration: int = 3600
) -> Session:
    """
    Create an automated session, deactivating any existing sessions.
    
    Args:
        db: Database session
        api_key_id: Optional API key ID to associate with the session
        user_id: Optional user ID to associate with the session
        requested_model: Optional model name or blockchain ID
        session_duration: Session duration in seconds (default: 1 hour)
        
    Returns:
        Session: The created session object
    """
    logger.info(f"Creating automated session for API key {api_key_id}, model: {requested_model}")
    
    try:
        # Get the target model using the model router
        target_model = model_router.get_target_model(requested_model)
        logger.info(f"Resolved target model: {target_model}")
        
        # If api_key_id provided and db is available, deactivate any existing sessions
        if api_key_id and db:
            await session_crud.deactivate_existing_sessions(db, api_key_id)
        
        # Get user's private key
        if db and user_id:
            private_key, using_fallback = await private_key_crud.get_private_key_with_fallback(db, user_id)
            
            if not private_key:
                raise ValueError("No private key found and no fallback key configured")
            
            # Prepare session data
            session_data = {
                "sessionDuration": session_duration,
                "failover": False,
                "directPayment": False
            }
            
            # Add private key to headers
            headers = {
                "X-Private-Key": private_key
            }
            
            try:
                # Create session with proxy router using the model session endpoint
                response = await execute_proxy_router_operation(
                    "POST",
                    f"blockchain/models/{target_model}/session",
                    headers=headers,
                    json=session_data,
                    max_retries=3
                )
                
                # Extract session ID from response
                blockchain_session_id = None
                
                if isinstance(response, dict):
                    blockchain_session_id = (response.get("sessionID") or 
                                         response.get("session", {}).get("id") or 
                                         response.get("id"))
                
                if not blockchain_session_id:
                    raise ValueError("No session ID found in proxy router response")
                
                # Store session in database
                expiry_time_with_tz = datetime.now(timezone.utc) + timedelta(seconds=session_duration)
                # Convert to naive datetime for DB compatibility
                expiry_time = expiry_time_with_tz.replace(tzinfo=None)
                session = await session_crud.create_session(
                    db=db,
                    session_id=blockchain_session_id,
                    api_key_id=api_key_id,
                    user_id=user_id,
                    model=target_model,
                    session_type="automated",
                    expires_at=expiry_time
                )
                
                logger.info(f"Successfully created automated session {blockchain_session_id}")
                return session
                
            except Exception as e:
                logger.error(f"Error creating session with proxy router: {e}")
                raise
        else:
            raise ValueError("Database session and user ID are required to create an automated session")
    except Exception as e:
        logger.error(f"Error creating automated session: {e}")
        raise

async def close_session(
    db: AsyncSession, 
    session_id: str
) -> bool:
    """
    Close an existing session.
    
    Args:
        db: Database session
        session_id: ID of the session to close
        
    Returns:
        bool: True if session was closed successfully, False otherwise
    """
    try:
        # Get session from database
        session = await session_crud.get_session(db, session_id)
        if not session:
            logger.warning(f"Session {session_id} not found")
            return False
            
        # Call proxy router to close session
        auth_header = base64.b64encode(b"user:pass").decode("utf-8")
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/json"
        }
        
        try:
            await execute_proxy_router_operation(
                "DELETE",
                f"/v1/sessions/{session_id}",
                headers=headers,
                max_retries=2
            )
            logger.info(f"Successfully closed session {session_id} at proxy level")
        except ValueError as proxy_error:
            # Check if this is a 404 error (session doesn't exist at proxy)
            if "404 Not Found" in str(proxy_error):
                logger.info(f"Session {session_id} not found at proxy level, considering already closed")
            else:
                # Log other errors but continue to mark session as inactive
                logger.warning(f"Error closing session at proxy level: {proxy_error}")
        
        # Mark session as inactive in database regardless of proxy result
        await session_crud.mark_session_inactive(db, session_id)
        
        logger.info(f"Successfully marked session {session_id} as inactive in database")
        return True
    
    except Exception as e:
        logger.error(f"Error closing session {session_id}: {e}")
        # Still mark as inactive in our database even if proxy router call fails
        try:
            await session_crud.mark_session_inactive(db, session_id)
        except Exception as inner_e:
            logger.error(f"Failed to mark session {session_id} as inactive: {inner_e}")
        return False

async def get_or_create_session(
    db: AsyncSession,
    api_key_id: int,
    requested_model: Optional[str] = None
) -> Session:
    """
    Get an existing active session or create a new one.
    
    Args:
        db: Database session
        api_key_id: API key ID to get/create session for
        requested_model: Optional model name or blockchain ID
        
    Returns:
        Session: The active session
    """
    # Try to get existing active session
    existing_session = await session_crud.get_active_session_by_api_key(db, api_key_id)
    
    # If session exists, is active, and not expired, return it
    if existing_session and not existing_session.is_expired:
        return existing_session
        
    # Otherwise create a new session
    return await create_automated_session(
        db=db,
        api_key_id=api_key_id,
        requested_model=requested_model
    ) 