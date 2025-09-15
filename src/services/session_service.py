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
    logger.info(f"[SESSION_DEBUG] Creating automated session for API key {api_key_id}, model: {requested_model}")
    logger.info(f"[SESSION_DEBUG] Proxy router URL: {settings.PROXY_ROUTER_URL}")
    logger.info(f"[SESSION_DEBUG] Using session duration: {session_duration}s")
    
    try:
        # Get the target model using the model router
        logger.info(f"[SESSION_DEBUG] About to resolve target model from: {requested_model}")
        target_model = await model_router.get_target_model(requested_model)
        logger.info(f"[SESSION_DEBUG] Resolved target model: {target_model}")
        
        # If api_key_id provided and db is available, deactivate any existing sessions
        if api_key_id and db:
            logger.info(f"[SESSION_DEBUG] Deactivating existing sessions for API key: {api_key_id}")
            await session_crud.deactivate_existing_sessions(db, api_key_id)
            logger.info("[SESSION_DEBUG] Existing sessions deactivated successfully")
        else:
            logger.warning(f"[SESSION_DEBUG] Cannot deactivate existing sessions - api_key_id: {'present' if api_key_id else 'missing'}, db: {'present' if db else 'missing'}")
        
        # Get user's private key
        if db and user_id:
            logger.info(f"[SESSION_DEBUG] Getting private key for user {user_id}")
            private_key, using_fallback = await private_key_crud.get_private_key_with_fallback(db, user_id)
            
            if not private_key:
                logger.error("[SESSION_DEBUG] No private key found and no fallback key configured")
                raise ValueError("No private key found and no fallback key configured")
            
            logger.info(f"[SESSION_DEBUG] Found private key (using fallback: {using_fallback})")
            
            # Prepare session data
            session_data = {
                "sessionDuration": session_duration,
                "failover": False,
                "directPayment": False
            }
            logger.info(f"[SESSION_DEBUG] Session data: {json.dumps(session_data)}")
            
            # Add private key to headers
            headers = {
                "X-Private-Key": private_key,
                "Content-Type": "application/json"
            }
            logger.info(f"[SESSION_DEBUG] Request headers prepared: {json.dumps({k: v for k, v in headers.items() if k != 'X-Private-Key'})}")
            
            try:
                # Create session with proxy router using the model session endpoint
                logger.info(f"[SESSION_DEBUG] Calling proxy router at: blockchain/models/{target_model}/session")
                
                # CRITICAL FIX: Ensure we're using the blockchain ID, not the model name
                # The proxy router expects hex blockchain IDs, not model names
                if not target_model.startswith("0x"):
                    logger.error(f"[SESSION_DEBUG] CRITICAL ERROR: target_model is not a blockchain ID: {target_model}")
                    logger.error(f"[SESSION_DEBUG] This will cause hex decoding errors in the proxy router")
                    raise ValueError(f"Invalid blockchain ID format: {target_model}. Expected hex string starting with '0x'")
                
                logger.info(f"[SESSION_DEBUG] Using blockchain ID: {target_model}")
                
                response = await execute_proxy_router_operation(
                    "POST",
                    f"blockchain/models/{target_model}/session",
                    headers=headers,
                    json_data=session_data,
                    max_retries=3
                )
                
                logger.info(f"[SESSION_DEBUG] Proxy router response: {json.dumps(response) if response else 'None'}")
                
                # Extract session ID from response
                blockchain_session_id = None
                
                if isinstance(response, dict):
                    blockchain_session_id = (response.get("sessionID") or 
                                         response.get("session", {}).get("id") or 
                                         response.get("id"))
                
                if not blockchain_session_id:
                    logger.error(f"[SESSION_DEBUG] No session ID found in proxy router response: {json.dumps(response)}")
                    raise ValueError("No session ID found in proxy router response")
                
                logger.info(f"[SESSION_DEBUG] Extracted blockchain session ID: {blockchain_session_id}")
                
                # Store session in database
                expiry_time_with_tz = datetime.now(timezone.utc) + timedelta(seconds=session_duration)
                # Convert to naive datetime for DB compatibility
                expiry_time = expiry_time_with_tz.replace(tzinfo=None)
                logger.info(f"[SESSION_DEBUG] Storing session in database with expiry: {expiry_time}")
                
                session = await session_crud.create_session(
                    db=db,
                    session_id=blockchain_session_id,
                    api_key_id=api_key_id,
                    user_id=user_id,
                    model=target_model,
                    session_type="automated",
                    expires_at=expiry_time
                )
                
                logger.info(f"[SESSION_DEBUG] Successfully created automated session {blockchain_session_id} with DB ID {session.id if session else 'None'}")
                return session
                
            except Exception as e:
                logger.error(f"[SESSION_DEBUG] Error creating session with proxy router: {e}")
                logger.exception(e)  # Log full stack trace
                
                # Try to diagnose proxy router connectivity
                try:
                    logger.info("[SESSION_DEBUG] Attempting direct connection to proxy router")
                    
                    # Define auth headers for raw request
                    auth_str = f"{settings.PROXY_ROUTER_USERNAME}:{settings.PROXY_ROUTER_PASSWORD}"
                    auth_b64 = base64.b64encode(auth_str.encode('ascii')).decode('ascii')
                    
                    raw_headers = {
                        "Authorization": f"Basic {auth_b64}",
                        "Content-Type": "application/json"
                    }
                    
                    # Make a direct health check request to diagnose connection issues
                    async with httpx.AsyncClient() as client:
                        try:
                            health_url = f"{settings.PROXY_ROUTER_URL}/healthcheck"
                            logger.info(f"[SESSION_DEBUG] Testing direct connection to proxy health endpoint: {health_url}")
                            health_response = await client.get(health_url, headers=raw_headers, timeout=5.0)
                            logger.info(f"[SESSION_DEBUG] Health check response: Status {health_response.status_code}, Body: {health_response.text[:200]}")
                        except Exception as health_err:
                            logger.error(f"[SESSION_DEBUG] Health check failed: {health_err}")
                        
                        # Try to get available models
                        try:
                            models_url = f"{settings.PROXY_ROUTER_URL}/v1/models"
                            logger.info(f"[SESSION_DEBUG] Testing available models: {models_url}")
                            models_response = await client.get(models_url, headers=raw_headers, timeout=5.0)
                            logger.info(f"[SESSION_DEBUG] Models API response: Status {models_response.status_code}, Body: {models_response.text[:200]}")
                        except Exception as models_err:
                            logger.error(f"[SESSION_DEBUG] Models check failed: {models_err}")
                except Exception as diag_err:
                    logger.error(f"[SESSION_DEBUG] Diagnostic connection test failed: {diag_err}")
                
                raise
        else:
            missing_items = []
            if not db:
                missing_items.append("db")
            if not user_id:
                missing_items.append("user_id")
            
            error_msg = f"Database session and user ID are required to create an automated session. Missing: {', '.join(missing_items)}"
            logger.error(f"[SESSION_DEBUG] {error_msg}")
            raise ValueError(error_msg)
    except Exception as e:
        logger.error(f"[SESSION_DEBUG] Fatal error creating automated session: {e}")
        logger.exception(e)  # Log full stack trace
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
            
        proxy_success = False
        try:
            # Use the correct POST endpoint for closing sessions
            # No need to manually set auth headers, execute_proxy_router_operation handles it
            response = await execute_proxy_router_operation(
                "POST",
                f"blockchain/sessions/{session_id}/close",
                max_retries=3
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
            f"blockchain/sessions/{session_id}",
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
    
    # Convert the new model to its ID form for comparison
    try:
        new_model_id = await model_router.get_target_model(new_model)
        logger.info(f"Resolved new model '{new_model}' to ID: {new_model_id}")
    except Exception as e:
        logger.error(f"Error resolving new model '{new_model}' to ID: {e}")
        logger.exception(e)
        # If we can't resolve the model ID, just use the original string
        new_model_id = new_model
    
    # Check if we actually need to switch models
    if current_session:
        current_model_id = current_session.model
        logger.info(f"Current session model ID: {current_model_id}")
        
        # If models are the same, just return the current session
        if current_model_id == new_model_id:
            logger.info(f"Current model ID ({current_model_id}) matches requested model ID ({new_model_id}), no switch needed")
            return current_session
        
        # Models are different, close current session
        logger.info(f"Models are different. Current: {current_model_id}, Requested: {new_model_id}")
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
    else:
        logger.info(f"No active session found for API key {api_key_id}")
    
    # Create new session
    return await create_automated_session(
        db=db,
        api_key_id=api_key_id,
        user_id=user_id,
        requested_model=new_model
    ) 