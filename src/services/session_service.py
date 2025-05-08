import asyncio
import logging
import httpx
import os
import json
from typing import Optional, Dict, Any, Tuple, List
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
    Close an existing session with enhanced validation.
    
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
            logger.warning(f"Session {session_id} not found in database")
            return False
            
        # Call proxy router to close session
        auth_header = base64.b64encode(b"user:pass").decode("utf-8")
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/json"
        }
        
        proxy_success = False
        try:
            response = await execute_proxy_router_operation(
                "DELETE",
                f"/v1/sessions/{session_id}",
                headers=headers,
                max_retries=3  # Increased retries
            )
            logger.info(f"Successfully closed session {session_id} at proxy level")
            proxy_success = True
        except ValueError as proxy_error:
            # Check if this is a 404 error (session doesn't exist at proxy)
            if "404 Not Found" in str(proxy_error):
                logger.info(f"Session {session_id} not found at proxy level, considering already closed")
                proxy_success = True
            else:
                # Log other errors but don't mark success
                logger.error(f"Error closing session at proxy level: {proxy_error}")
                # Try to verify session status at proxy
                try:
                    status = await check_proxy_session_status(session_id)
                    if status.get("closed", False):
                        logger.info(f"Session {session_id} verified as closed despite error")
                        proxy_success = True
                except Exception as verify_error:
                    logger.error(f"Failed to verify session status: {verify_error}")
        
        # Only mark session as inactive if proxy closure was successful
        if proxy_success:
            await session_crud.mark_session_inactive(db, session_id)
            logger.info(f"Successfully marked session {session_id} as inactive in database")
            return True
        else:
            # If proxy failed but session is expired, still mark inactive in DB
            if session.is_expired:
                logger.warning(f"Session {session_id} is expired, marking inactive despite proxy failure")
                await session_crud.mark_session_inactive(db, session_id)
                return True
            else:
                # If proxy failed and session isn't expired, don't update DB to maintain consistency
                logger.warning(f"Not marking session {session_id} as inactive in DB due to proxy failure")
                return False
    
    except Exception as e:
        logger.error(f"Error closing session {session_id}: {e}")
        try:
            # On critical errors, still try to mark the session inactive
            await session_crud.mark_session_inactive(db, session_id)
            logger.warning(f"Marked session {session_id} inactive despite error: {e}")
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

async def check_proxy_session_status(session_id: str) -> Dict[str, Any]:
    """
    Check the status of a session directly in the proxy router.
    
    Args:
        session_id: ID of the session to check
        
    Returns:
        Dict with session status information, including 'closed' boolean
    """
    try:
        response = await execute_proxy_router_operation(
            "GET",
            f"/v1/sessions/{session_id}",
            max_retries=2
        )
        
        if response and isinstance(response, dict):
            # Check if session is closed based on ClosedAt field
            closed = False
            if "ClosedAt" in response and response["ClosedAt"] > 0:
                closed = True
            
            return {
                "exists": True,
                "closed": closed,
                "data": response
            }
        else:
            return {"exists": False, "closed": True, "data": None}
    except Exception as e:
        if "404 Not Found" in str(e):
            # Session doesn't exist in proxy router
            return {"exists": False, "closed": True, "data": None}
        logger.error(f"Error checking session status for {session_id}: {e}")
        return {"exists": False, "closed": False, "error": str(e)}

async def verify_session_status(db: AsyncSession, session_id: str) -> bool:
    """
    Verify session status in both database and proxy router.
    
    Args:
        db: Database session
        session_id: ID of the session to verify
        
    Returns:
        bool: True if session is valid and active, False otherwise
    """
    # Check database status
    session = await session_crud.get_session(db, session_id)
    if not session or not session.is_active or session.is_expired:
        logger.info(f"Session {session_id} is invalid in database")
        return False
        
    # Check proxy router status
    proxy_status = await check_proxy_session_status(session_id)
    return proxy_status.get("exists", False) and not proxy_status.get("closed", True)

async def synchronize_sessions(db: AsyncSession):
    """
    Synchronize session states between database and proxy router.
    
    Args:
        db: Database session
    """
    logger.info("Starting session synchronization")
    
    # Get all sessions marked as active in database
    active_sessions = await session_crud.get_all_active_sessions(db)
    
    for session in active_sessions:
        # Verify each session's status in proxy router
        try:
            proxy_status = await check_proxy_session_status(session.id)
            
            # Session doesn't exist or is closed in proxy router
            if not proxy_status.get("exists", False) or proxy_status.get("closed", True):
                logger.info(f"Session {session.id} is closed in proxy but active in DB, synchronizing")
                await session_crud.mark_session_inactive(db, session.id)
        except Exception as e:
            logger.error(f"Error checking session {session.id} in proxy: {e}")
            # Don't automatically mark as inactive on error

async def switch_model(
    db: AsyncSession, 
    api_key_id: int, 
    user_id: int,
    new_model: str
) -> Session:
    """
    Safely switch from one model to another by ensuring clean session closure.
    
    Args:
        db: Database session
        api_key_id: API key ID associated with the session
        user_id: User ID associated with the session
        new_model: ID or name of the model to switch to
        
    Returns:
        Session: The newly created session object
    """
    logger.info(f"Switching to model {new_model} for API key {api_key_id}")
    
    # Get current active session
    current_session = await session_crud.get_active_session_by_api_key(db, api_key_id)
    
    # Close current session if it exists
    if current_session:
        logger.info(f"Found existing session {current_session.id}, closing before switching models")
        # Try closing up to 3 times
        for attempt in range(3):
            success = await close_session(db, current_session.id)
            if success:
                logger.info(f"Successfully closed session {current_session.id} on attempt {attempt+1}")
                break
            logger.warning(f"Failed to close session on attempt {attempt+1}, retrying...")
            await asyncio.sleep(1)  # Wait before retry
        
        # Verify closure with proxy router
        proxy_status = await check_proxy_session_status(current_session.id)
        if not proxy_status.get("closed", False) and proxy_status.get("exists", False):
            logger.error(f"Failed to close session {current_session.id} in proxy after multiple attempts")
            # Force mark as inactive in DB to prevent orphaned sessions
            await session_crud.mark_session_inactive(db, current_session.id)
    
    # Create new session
    return await create_automated_session(
        db=db,
        api_key_id=api_key_id,
        user_id=user_id,
        requested_model=new_model
    ) 