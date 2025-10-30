import asyncio
import json
from typing import Optional, Dict, Any, Tuple, List
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone

from ..db.models import Session
from ..db.models import UserAutomationSettings
from ..core.config import settings
from ..crud import session as session_crud
from ..crud import private_key as private_key_crud
from ..crud import automation as automation_crud
from ..services import proxy_router_service
from ..core.model_routing import model_router
from ..core.logging_config import get_api_logger
from ..core.redis_cache import get_cached_session, cache_session, invalidate_session_cache

logger = get_api_logger()

def _serialize_session(session: Session) -> dict:
    """Helper function to serialize a Session object to a JSON-compatible dict"""
    return {
        'id': session.id,
        'api_key_id': session.api_key_id,
        'user_id': session.user_id,
        'model': session.model,
        'type': session.type,
        'created_at': session.created_at.isoformat() if session.created_at else None,
        'expires_at': session.expires_at.isoformat() if session.expires_at else None,
        'is_active': session.is_active
    }

async def _cache_session_object(session: Session) -> None:
    """Helper function to cache a Session object (best effort, non-blocking)"""
    if session and session.api_key_id:
        session_data = _serialize_session(session)
        # Calculate remaining TTL based on expiry time
        if session.expires_at:
            remaining_ttl = int((session.expires_at - datetime.utcnow()).total_seconds())
            if remaining_ttl > 0:
                await cache_session(session.api_key_id, session_data, ttl=remaining_ttl)
            else:
                logger.debug("Session already expired, not caching",
                           session_id=session.id)

async def get_automation_settings(
    db: AsyncSession,
    user_id: int
) -> Optional[UserAutomationSettings]:
    if not settings.AUTOMATION_FEATURE_ENABLED:
        logger.info("Automation feature is disabled system-wide",
                   user_id=user_id,
                   event_type="automation_disabled_system_wide")
        return None
        
    # Check if automation is enabled for the user in their settings
    automation_settings = await automation_crud.get_automation_settings(db, user_id)
    
    # If settings don't exist yet, create them with automation enabled by default
    if not automation_settings:
        logger.info("No automation settings found for user - creating default settings",
                   user_id=user_id,
                   event_type="automation_settings_created")
        automation_settings = await automation_crud.create_automation_settings(
            db=db,
            user_id=user_id,
            is_enabled=True,  # Enable automation by default
            session_duration=3600  # Default 1 hour session
        )
    # If settings exist but automation is disabled, log and return None
    elif not automation_settings.is_enabled:
        logger.info("Automation is explicitly disabled for user",
                   user_id=user_id,
                   event_type="automation_disabled_for_user")
        return None
    
    return automation_settings

async def get_session_for_api_key(
    db: AsyncSession,
    api_key_id: int,
    user_id: int,
    requested_model: Optional[str] = None,
    session_duration: Optional[int] = None,
    model_type: Optional[str] = "LLM"
) -> Optional[Session]:
    session_logger = logger.bind(api_key_id=api_key_id, requested_model=requested_model)
    
    # ========================================================================
    # REDIS CACHE LAYER (Two-Way Door Pattern)
    # ========================================================================
    # Try to get cached session from Redis first
    cached_session_data = await get_cached_session(api_key_id)
    
    if cached_session_data:
        session_logger.debug("Session cache HIT for API key",
                           session_id=cached_session_data.get('id'),
                           event_type="session_cache_hit")
        
        # Check if cached session is expired
        expires_at = datetime.fromisoformat(cached_session_data['expires_at'])
        if datetime.utcnow() > expires_at:
            session_logger.info("Cached session is expired, invalidating cache",
                               session_id=cached_session_data.get('id'),
                               event_type="cached_session_expired")
            await invalidate_session_cache(api_key_id)
        else:
            # Check if model matches
            requested_model_id = await model_router.get_target_model(requested_model, model_type)
            if cached_session_data['model'] == requested_model_id:
                session_logger.info("Cached session is valid and model matches (fast path)",
                                   session_id=cached_session_data.get('id'),
                                   model_id=requested_model_id,
                                   event_type="cached_session_valid")
                # Reconstruct Session object from cached data
                # Note: This is a "light" Session object without relationships loaded
                # But that's fine for chat completion - we only need id, model, expires_at
                session = Session(
                    id=cached_session_data['id'],
                    api_key_id=cached_session_data['api_key_id'],
                    user_id=cached_session_data['user_id'],
                    model=cached_session_data['model'],
                    type=cached_session_data['type'],
                    created_at=datetime.fromisoformat(cached_session_data['created_at']),
                    expires_at=expires_at,
                    is_active=cached_session_data['is_active']
                )
                return session
            else:
                session_logger.info("Cached session model mismatch, invalidating and creating new",
                                   cached_model=cached_session_data['model'],
                                   requested_model_id=requested_model_id,
                                   event_type="cached_session_model_mismatch")
                await close_session(db, cached_session_data['id'])
                return await create_automated_session(db, api_key_id, user_id, requested_model, session_duration, model_type=model_type)
    
    # ========================================================================
    # DATABASE VALIDATION LAYER (Cache miss or fallback)
    # ========================================================================
    session_logger.debug("Session cache MISS, querying database",
                        event_type="session_cache_miss")
    
    session = await session_crud.get_active_session_by_api_key(db, api_key_id)
    
    if session and session.is_active and not session.is_expired:
        session_logger.info("Found active session in database",
                           session_id=session.id,
                           session_model=session.model,
                           event_type="active_session_found")
        requested_model_id = await model_router.get_target_model(requested_model, model_type)
        if session.model == requested_model_id:
            session_logger.info("Session is already using the requested model",
                               session_id=session.id,
                               model_id=requested_model_id,
                               event_type="session_model_match")
            
            # Cache the session for next request
            await _cache_session_object(session)
            
            return session
        else:
            session_logger.info("Session model mismatch, closing and creating new session",
                               session_id=session.id,
                               current_model=session.model,
                               requested_model_id=requested_model_id,
                               event_type="session_model_mismatch")
            await close_session(db, session.id)
            return await create_automated_session(db, api_key_id, user_id, requested_model, session_duration, model_type=model_type)
    
    # No explicit logging here - create_automated_session will log with complete details
    return await create_automated_session(db, api_key_id, user_id, requested_model, session_duration, model_type=model_type)

async def create_automated_session(
    db: AsyncSession,
    api_key_id: int, 
    user_id: int,
    requested_model: str,
    session_duration: Optional[int] = None,
    model_type: Optional[str] = "LLM"
) -> Optional[Session]:
    """
    Create an automated session, deactivating any existing sessions.
    
    Args:
        db: Database session
        api_key_id: Optional API key ID to associate with the session
        user_id: Optional user ID to associate with the session
        requested_model: Optional model name or blockchain ID
        
    Returns:
        Session: The created session object
    """

    if not session_duration:
        # Check system-wide feature flag first
        automation_settings = await get_automation_settings(db, user_id)

        if not automation_settings:
            logger.info("Automation is disabled for user",
                       user_id=user_id,
                       event_type="automation_disabled")
            return None
        
        # Automation is enabled - create a new session
        logger.info("Automation enabled for user - creating new session",
                   user_id=user_id,
                   session_duration=automation_settings.session_duration,
                   event_type="automation_enabled")
        
        # Create new session with requested model
        session_duration = automation_settings.session_duration
    
    create_logger = logger.bind(api_key_id=api_key_id, user_id=user_id, requested_model=requested_model)
    create_logger.info("Creating automated session",
                      api_key_id=api_key_id,
                      requested_model=requested_model,
                      session_duration=session_duration,
                      proxy_router_url=settings.PROXY_ROUTER_URL,
                      event_type="automated_session_creation_start")
    
    try:
        # Get the target model using the model router
        create_logger.info("Resolving target model",
                          requested_model=requested_model,
                          event_type="model_resolution_start")
        target_model = await model_router.get_target_model(requested_model, model_type)
        create_logger.info("Target model resolved successfully",
                          target_model=target_model,
                          requested_model=requested_model,
                          event_type="model_resolved")
        
        create_logger.info("Deactivating existing sessions for API key",
                          api_key_id=api_key_id,
                          event_type="existing_sessions_deactivation_start")
        await session_crud.deactivate_existing_sessions(db, api_key_id)
        create_logger.info("Existing sessions deactivated successfully",
                          event_type="existing_sessions_deactivated")
        
        # Get user's private key
        create_logger.info("Getting private key for user",
                          user_id=user_id,
                          event_type="private_key_lookup_start")
        private_key, using_fallback = await private_key_crud.get_private_key_with_fallback(db, user_id)
        
        if not private_key:
            create_logger.error("No private key found and no fallback key configured",
                               user_id=user_id,
                               event_type="private_key_not_available")
            raise ValueError("No private key found and no fallback key configured")
        
        create_logger.info("Found private key for session creation",
                          user_id=user_id,
                          using_fallback=using_fallback,
                          event_type="private_key_found")
        
        # Prepare session data
        session_data = {
            "sessionDuration": session_duration,
            "failover": False,
            "directPayment": False
        }
        create_logger.debug("Session data prepared",
                           session_data=session_data,
                           event_type="session_data_prepared")
        
        try:
            # Create session with proxy router using the model session endpoint
            create_logger.info("Calling proxy router to create session",
                              target_model=target_model,
                              endpoint=f"blockchain/models/{target_model}/session",
                              event_type="proxy_session_creation_start")
            
            # CRITICAL FIX: Ensure we're using the blockchain ID, not the model name
            # The proxy router expects hex blockchain IDs, not model names
            if not target_model.startswith("0x"):
                create_logger.error("CRITICAL ERROR: target_model is not a blockchain ID",
                                   target_model=target_model,
                                   expected_format="0x...",
                                   event_type="invalid_blockchain_id_format")
                raise ValueError(f"Invalid blockchain ID format: {target_model}. Expected hex string starting with '0x'")
            
            create_logger.debug("Using valid blockchain ID",
                               blockchain_id=target_model,
                               event_type="blockchain_id_validated")
            
            response = await proxy_router_service.openSession(
                target_model=target_model,
                session_duration=session_duration,
                user_id=user_id,
                db=db,
                failover=False,
                direct_payment=False
            )
            
            create_logger.info("Proxy router response received",
                              response_data=response,
                              event_type="proxy_session_response")
            
            # Extract session ID from response
            blockchain_session_id = response.get("sessionID")
            
            if not blockchain_session_id:
                create_logger.error("No session ID found in proxy router response",
                                   response_data=response,
                                   event_type="session_id_missing_in_response")
                raise ValueError("No session ID found in proxy router response")
            
            create_logger.info("Extracted blockchain session ID",
                              blockchain_session_id=blockchain_session_id,
                              event_type="session_id_extracted")
            
            # Store session in database
            expiry_time_with_tz = datetime.now(timezone.utc) + timedelta(seconds=session_duration)
            # Convert to naive datetime for DB compatibility
            expiry_time = expiry_time_with_tz.replace(tzinfo=None)
            create_logger.info("Storing session in database",
                              session_id=blockchain_session_id,
                              expiry_time=expiry_time.isoformat(),
                              event_type="session_db_storage_start")
            
            session = await session_crud.create_session(
                db=db,
                session_id=blockchain_session_id,
                api_key_id=api_key_id,
                user_id=user_id,
                model=target_model,
                session_type="automated",
                expires_at=expiry_time
            )
            
            create_logger.info("Successfully created automated session",
                              blockchain_session_id=blockchain_session_id,
                              db_session_id=session.id if session else None,
                              target_model=target_model,
                              event_type="automated_session_created")
            
            # Cache the newly created session (best effort, non-blocking)
            await _cache_session_object(session)
            
            return session
            
        except proxy_router_service.ProxyRouterServiceError as e:
            create_logger.error("Error creating session with proxy router",
                               error=str(e),
                               error_type=e.error_type,
                               status_code=e.status_code,
                               target_model=target_model,
                               event_type="proxy_session_creation_error",
                               exc_info=True)
            raise
        
    except Exception as e:
        create_logger.error("Fatal error creating automated session",
                           error=str(e),
                           api_key_id=api_key_id,
                           user_id=user_id,
                           requested_model=requested_model,
                           event_type="automated_session_fatal_error",
                           exc_info=True)
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
    close_logger = logger.bind(session_id=session_id)
    try:
        # Get session from database
        session = await session_crud.get_session_by_id(db, session_id)
        if not session:
            close_logger.warning("Session not found in database",
                               session_id=session_id,
                               event_type="session_not_found_for_close")
            return False
            
        proxy_success = False
        try:
            # Use the proxy router service to close the session
            response = await proxy_router_service.closeSession(session_id)
            close_logger.info("Successfully closed session at proxy level",
                             session_id=session_id,
                             event_type="proxy_session_closed")
            proxy_success = True
        except proxy_router_service.ProxyRouterServiceError as proxy_error:
            # Log other errors but don't mark success
            close_logger.error("Error closing session at proxy level",
                              session_id=session_id,
                              error=str(proxy_error),
                              error_type=proxy_error.error_type,
                              event_type="proxy_session_close_error")
            # Try to verify session status at proxy
            try:
                status = await check_proxy_session_status(session_id)
                if status.get("closed", False):
                    close_logger.info("Session verified as closed despite error",
                                     session_id=session_id,
                                     event_type="session_verified_closed")
                    proxy_success = True
            except Exception as verify_error:
                close_logger.error("Failed to verify session status",
                                   session_id=session_id,
                                   error=str(verify_error),
                                   event_type="session_status_verification_error")
        
        # Only mark session as inactive if proxy closure was successful
        if proxy_success or session.is_expired:
            await session_crud.mark_session_inactive(db, session_id)
            close_logger.info("Successfully marked session as inactive in database",
                             session_id=session_id,
                             event_type="session_marked_inactive")
            
            # Invalidate session cache (best effort, non-blocking)
            if session.api_key_id:
                await invalidate_session_cache(session.api_key_id)
            
            return True
        close_logger.warning("Not marking session as inactive in DB due to proxy failure",
                            session_id=session_id,
                            event_type="session_not_marked_inactive")
        return False
    
    except Exception as e:
        close_logger.error("Error getting session for closure",
                          session_id=session_id,
                          error=str(e),
                          event_type="session_close_error")
        try:
            # On critical errors, still try to mark the session inactive
            await session_crud.mark_session_inactive(db, session_id)
            close_logger.warning("Marked session inactive despite error",
                                session_id=session_id,
                                error=str(e),
                                event_type="session_marked_inactive_despite_error")
        except Exception as inner_e:
            close_logger.error("Failed to mark session as inactive",
                              session_id=session_id,
                              error=str(inner_e),
                              event_type="session_mark_inactive_failed")
        return False

async def check_proxy_session_status(session_id: str) -> Dict[str, Any]:
    """
    Check the status of a session directly in the proxy router.
    
    Args:
        session_id: ID of the session to check
        
    Returns:
        Dict with session status information, including 'closed' boolean
    """
    try:
        response = await proxy_router_service.getSessionStatus(session_id)
        
        if response and isinstance(response, dict):
            if "BidID" in response and response["BidID"] == "0x0000000000000000000000000000000000000000000000000000000000000000":
                return {"exists": False, "closed": True, "data": None}

            closed = False
            if "ClosedAt" in response and response["ClosedAt"] > 0:
                closed = True
            
            return {
                "exists": True,
                "closed": closed,
                "data": response
            }
        else:
            status_logger = logger.bind(session_id=session_id)
            status_logger.error("Invalid response from proxy router",
                              session_id=session_id,
                              response=response,
                              event_type="invalid_proxy_response")
            return {"exists": False, "closed": True, "data": None}
    except proxy_router_service.ProxyRouterServiceError as e:
        status_logger.error("Error checking session status",
                           session_id=session_id,
                           error=str(e),
                           error_type=e.error_type,
                           event_type="session_status_check_error")
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
    session = await session_crud.get_session_by_id(db, session_id)
    if not session or not session.is_active or session.is_expired:
        verify_logger = logger.bind(session_id=session_id)
        verify_logger.info("Session is invalid in database",
                          session_id=session_id,
                          event_type="session_invalid_in_database")
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
    sync_logger = logger.bind(component="session_synchronization")
    sync_logger.info("Starting session synchronization",
                    event_type="session_sync_start")
    
    # Get all sessions marked as active in database
    active_sessions = await session_crud.get_all_active_sessions(db)
    
    for session in active_sessions:
        # Verify each session's status in proxy router
        try:
            proxy_status = await check_proxy_session_status(session.id)
            
            # Session doesn't exist or is closed in proxy router
            if not proxy_status.get("exists", False) or proxy_status.get("closed", True):
                sync_logger.info("Session is closed in proxy but active in DB, synchronizing",
                                session_id=session.id,
                                event_type="session_sync_inconsistency")
                await session_crud.mark_session_inactive(db, session.id)
        except Exception as e:
            sync_logger.error("Error checking session in proxy",
                             session_id=session.id,
                             error=str(e),
                             event_type="session_proxy_check_error")
            # Don't automatically mark as inactive on error
