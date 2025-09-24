import asyncio
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
from ..core.structured_logger import PROXY_LOG

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
    session_log = PROXY_LOG.named("CREATE").with_fields(
        api_key_id=api_key_id,
        user_id=user_id,
        requested_model=requested_model,
        session_duration=session_duration,
        event_type="session_creation"
    )
    
    session_log.infof("Creating automated session for API key %s, model: %s", api_key_id, requested_model)
    session_log.infof("Proxy router URL: %s", settings.PROXY_ROUTER_URL)
    session_log.infof("Using session duration: %ds", session_duration)
    
    try:
        # Get the target model using the model router
        session_log.debugf("About to resolve target model from: %s", requested_model)
        target_model = await model_router.get_target_model(requested_model)
        session_log.with_fields(target_model=target_model).infof("Resolved target model: %s", target_model)
        
        # If api_key_id provided and db is available, deactivate any existing sessions
        if api_key_id and db:
            session_log.with_fields(
                event_type="session_deactivation",
                api_key_id=api_key_id
            ).infof("[SESSION_DEBUG] Deactivating existing sessions for API key: %d", api_key_id)
            await session_crud.deactivate_existing_sessions(db, api_key_id)
            session_log.with_fields(
                event_type="session_deactivation",
                status="success"
            ).info("[SESSION_DEBUG] Existing sessions deactivated successfully")
        else:
            session_log.with_fields(
                event_type="session_deactivation",
                status="skipped",
                api_key_id_present=bool(api_key_id),
                db_present=bool(db)
            ).warnf("[SESSION_DEBUG] Cannot deactivate existing sessions - api_key_id: %s, db: %s", 'present' if api_key_id else 'missing', 'present' if db else 'missing')
        
        # Get user's private key
        if db and user_id:
            session_log.with_fields(
                event_type="private_key_retrieval",
                user_id=user_id
            ).infof("[SESSION_DEBUG] Getting private key for user %d", user_id)
            private_key, using_fallback = await private_key_crud.get_private_key_with_fallback(db, user_id)
            
            if not private_key:
                session_log.with_fields(
                    event_type="private_key_retrieval",
                    status="error",
                    reason="no_key_no_fallback"
                ).error("[SESSION_DEBUG] No private key found and no fallback key configured")
                raise ValueError("No private key found and no fallback key configured")
            
            session_log.with_fields(
                event_type="private_key_retrieval",
                status="success",
                using_fallback=using_fallback
            ).infof("[SESSION_DEBUG] Found private key (using fallback: %s)", using_fallback)
            
            # Prepare session data
            session_data = {
                "sessionDuration": session_duration,
                "failover": False,
                "directPayment": False
            }
            session_log.with_fields(
                event_type="session_data_debug",
                session_data=session_data
            ).infof("[SESSION_DEBUG] Session data: %s", json.dumps(session_data))
            
            # Add private key to headers
            headers = {
                "X-Private-Key": private_key,
                "Content-Type": "application/json"
            }
            session_log.with_fields(
                event_type="session_headers_debug",
                headers_safe={k: v for k, v in headers.items() if k != 'X-Private-Key'}
            ).infof("[SESSION_DEBUG] Request headers prepared: %s", json.dumps({k: v for k, v in headers.items() if k != 'X-Private-Key'}))
            
            try:
                # Create session with proxy router using the model session endpoint
                session_log.with_fields(
                    event_type="proxy_router_call",
                    endpoint=f"blockchain/models/{target_model}/session",
                    target_model=target_model
                ).infof("[SESSION_DEBUG] Calling proxy router at: blockchain/models/%s/session", target_model)
                
                # CRITICAL FIX: Ensure we're using the blockchain ID, not the model name
                # The proxy router expects hex blockchain IDs, not model names
                if not target_model.startswith("0x"):
                    session_log.with_fields(
                        event_type="model_validation",
                        target_model=target_model,
                        status="critical_error",
                        issue="not_blockchain_id"
                    ).errorf("[SESSION_DEBUG] CRITICAL ERROR: target_model is not a blockchain ID: %s", target_model)
                    session_log.with_fields(
                        event_type="model_validation",
                        warning="hex_decoding_errors_expected"
                    ).error("[SESSION_DEBUG] This will cause hex decoding errors in the proxy router")
                    raise ValueError(f"Invalid blockchain ID format: {target_model}. Expected hex string starting with '0x'")
                
                session_log.with_fields(
                    event_type="model_validation",
                    target_model=target_model,
                    status="valid_blockchain_id"
                ).infof("[SESSION_DEBUG] Using blockchain ID: %s", target_model)
                
                response = await execute_proxy_router_operation(
                    "POST",
                    f"blockchain/models/{target_model}/session",
                    headers=headers,
                    json_data=session_data,
                    max_retries=3
                )
                
                session_log.with_fields(
                    event_type="proxy_router_response",
                    response=response
                ).infof("[SESSION_DEBUG] Proxy router response: %s", json.dumps(response) if response else 'None')
                
                # Extract session ID from response
                blockchain_session_id = None
                
                if isinstance(response, dict):
                    blockchain_session_id = (response.get("sessionID") or 
                                         response.get("session", {}).get("id") or 
                                         response.get("id"))
                
                if not blockchain_session_id:
                    session_log.with_fields(
                        event_type="proxy_router_response",
                        status="error",
                        issue="no_session_id",
                        response=response
                    ).errorf("[SESSION_DEBUG] No session ID found in proxy router response: %s", json.dumps(response))
                    raise ValueError("No session ID found in proxy router response")
                
                session_log.with_fields(
                    event_type="session_extraction",
                    blockchain_session_id=blockchain_session_id
                ).infof("[SESSION_DEBUG] Extracted blockchain session ID: %s", blockchain_session_id)
                
                # Store session in database
                expiry_time_with_tz = datetime.now(timezone.utc) + timedelta(seconds=session_duration)
                # Convert to naive datetime for DB compatibility
                expiry_time = expiry_time_with_tz.replace(tzinfo=None)
                session_log.with_fields(
                    event_type="session_storage",
                    expiry_time=expiry_time.isoformat() if expiry_time else None
                ).infof("[SESSION_DEBUG] Storing session in database with expiry: %s", expiry_time)
                
                session = await session_crud.create_session(
                    db=db,
                    session_id=blockchain_session_id,
                    api_key_id=api_key_id,
                    user_id=user_id,
                    model=target_model,
                    session_type="automated",
                    expires_at=expiry_time
                )
                
                session_log.with_fields(
                    event_type="session_creation_complete",
                    blockchain_session_id=blockchain_session_id,
                    db_session_id=session.id if session else None,
                    status="success"
                ).infof("[SESSION_DEBUG] Successfully created automated session %s with DB ID %s", blockchain_session_id, session.id if session else 'None')
                return session
                
            except Exception as e:
                session_log.with_fields(
                    event_type="proxy_router_error",
                    error=str(e)
                ).errorf("[SESSION_DEBUG] Error creating session with proxy router: %s", e)
                # Exception details already logged above
                
                # Try to diagnose proxy router connectivity
                try:
                    session_log.with_fields(
                        event_type="proxy_diagnostic",
                        action="direct_connection_test"
                    ).info("[SESSION_DEBUG] Attempting direct connection to proxy router")
                    
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
                            session_log.with_fields(
                                event_type="proxy_health_test",
                                health_url=health_url
                            ).infof("[SESSION_DEBUG] Testing direct connection to proxy health endpoint: %s", health_url)
                            health_response = await client.get(health_url, headers=raw_headers, timeout=5.0)
                            session_log.with_fields(
                                event_type="proxy_health_test",
                                status_code=health_response.status_code,
                                response_preview=health_response.text[:200]
                            ).infof("[SESSION_DEBUG] Health check response: Status %d, Body: %s", health_response.status_code, health_response.text[:200])
                        except Exception as health_err:
                            session_log.with_fields(
                                event_type="proxy_health_test",
                                status="failed",
                                error=str(health_err)
                            ).errorf("[SESSION_DEBUG] Health check failed: %s", health_err)
                        
                        # Try to get available models
                        try:
                            models_url = f"{settings.PROXY_ROUTER_URL}/v1/models"
                            session_log.with_fields(
                                event_type="proxy_models_test",
                                models_url=models_url
                            ).infof("[SESSION_DEBUG] Testing available models: %s", models_url)
                            models_response = await client.get(models_url, headers=raw_headers, timeout=5.0)
                            session_log.with_fields(
                                event_type="proxy_models_test",
                                status_code=models_response.status_code,
                                response_preview=models_response.text[:200]
                            ).infof("[SESSION_DEBUG] Models API response: Status %d, Body: %s", models_response.status_code, models_response.text[:200])
                        except Exception as models_err:
                            session_log.with_fields(
                                event_type="proxy_models_test",
                                status="failed",
                                error=str(models_err)
                            ).errorf("[SESSION_DEBUG] Models check failed: %s", models_err)
                except Exception as diag_err:
                    session_log.with_fields(
                        event_type="proxy_diagnostic",
                        status="failed",
                        error=str(diag_err)
                    ).errorf("[SESSION_DEBUG] Diagnostic connection test failed: %s", diag_err)
                
                raise
        else:
            missing_items = []
            if not db:
                missing_items.append("db")
            if not user_id:
                missing_items.append("user_id")
            
            error_msg = f"Database session and user ID are required to create an automated session. Missing: {', '.join(missing_items)}"
            session_log.with_fields(
                event_type="session_creation_validation",
                status="error",
                missing_items=missing_items
            ).errorf("[SESSION_DEBUG] %s", error_msg)
            raise ValueError(error_msg)
    except Exception as e:
        session_log.with_fields(
            event_type="session_creation_fatal",
            error=str(e)
        ).errorf("[SESSION_DEBUG] Fatal error creating automated session: %s", e)
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
            session_log.with_fields(
                event_type="session_close",
                session_id=session_id,
                status="not_found_in_db"
            ).warnf("Session %s not found in database", session_id)
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
            session_log.with_fields(
                event_type="session_close",
                session_id=session_id,
                status="success",
                level="proxy"
            ).infof("Successfully closed session %s at proxy level", session_id)
            proxy_success = True
        except ValueError as proxy_error:
            # Check if this is a 404 error (session doesn't exist at proxy)
            if "404 Not Found" in str(proxy_error):
                session_log.with_fields(
                    event_type="session_close",
                    session_id=session_id,
                    status="not_found_at_proxy",
                    assumption="already_closed"
                ).infof("Session %s not found at proxy level, considering already closed", session_id)
                proxy_success = True
            else:
                # Log other errors but don't mark success
                session_log.with_fields(
                    event_type="session_close",
                    session_id=session_id,
                    status="proxy_error",
                    error=str(proxy_error)
                ).errorf("Error closing session at proxy level: %s", proxy_error)
                # Try to verify session status at proxy
                try:
                    status = await check_proxy_session_status(session_id)
                    if status.get("closed", False):
                        session_log.with_fields(
                            event_type="session_close_verification",
                            session_id=session_id,
                            status="verified_closed"
                        ).infof("Session %s verified as closed despite error", session_id)
                        proxy_success = True
                except Exception as verify_error:
                    session_log.with_fields(
                        event_type="session_close_verification",
                        session_id=session_id,
                        status="verification_failed",
                        error=str(verify_error)
                    ).errorf("Failed to verify session status: %s", verify_error)
        
        # Only mark session as inactive if proxy closure was successful
        if proxy_success:
            await session_crud.mark_session_inactive(db, session_id)
            session_log.with_fields(
                event_type="session_database_update",
                session_id=session_id,
                status="marked_inactive"
            ).infof("Successfully marked session %s as inactive in database", session_id)
            return True
        else:
            # If proxy failed but session is expired, still mark inactive in DB
            if session.is_expired:
                session_log.with_fields(
                    event_type="session_database_update",
                    session_id=session_id,
                    status="expired_marked_inactive",
                    reason="proxy_failure"
                ).warnf("Session %s is expired, marking inactive despite proxy failure", session_id)
                await session_crud.mark_session_inactive(db, session_id)
                return True
            else:
                # If proxy failed and session isn't expired, don't update DB to maintain consistency
                session_log.with_fields(
                    event_type="session_database_update",
                    session_id=session_id,
                    status="not_marked_inactive",
                    reason="proxy_failure"
                ).warnf("Not marking session %s as inactive in DB due to proxy failure", session_id)
                return False
    
    except Exception as e:
        session_log.with_fields(
            event_type="session_close_error",
            session_id=session_id,
            error=str(e)
        ).errorf("Error closing session %s: %s", session_id, e)
        try:
            # On critical errors, still try to mark the session inactive
            await session_crud.mark_session_inactive(db, session_id)
            session_log.with_fields(
                event_type="session_database_update",
                session_id=session_id,
                status="marked_inactive_despite_error",
                error=str(e)
            ).warnf("Marked session %s inactive despite error: %s", session_id, e)
        except Exception as inner_e:
            session_log.with_fields(
                event_type="session_database_update",
                session_id=session_id,
                status="failed_to_mark_inactive",
                error=str(inner_e)
            ).errorf("Failed to mark session %s as inactive: %s", session_id, inner_e)
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
        session_log.with_fields(
            event_type="session_status_check",
            session_id=session_id,
            status="error",
            error=str(e)
        ).errorf("Error checking session status for %s: %s", session_id, e)
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
        session_log.with_fields(
            event_type="session_validation",
            session_id=session_id,
            status="invalid_in_database"
        ).infof("Session %s is invalid in database", session_id)
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
    sync_log = PROXY_LOG.named("SYNC")
    sync_log.with_fields(
        event_type="session_sync",
        action="starting"
    ).info("Starting session synchronization")
    
    # Get all sessions marked as active in database
    active_sessions = await session_crud.get_all_active_sessions(db)
    
    for session in active_sessions:
        # Verify each session's status in proxy router
        try:
            proxy_status = await check_proxy_session_status(session.id)
            
            # Session doesn't exist or is closed in proxy router
            if not proxy_status.get("exists", False) or proxy_status.get("closed", True):
                sync_log.with_fields(
                    event_type="session_sync_mismatch",
                    session_id=session.id,
                    db_status="active",
                    proxy_status="closed"
                ).infof("Session %s is closed in proxy but active in DB, synchronizing", session.id)
                await session_crud.mark_session_inactive(db, session.id)
        except Exception as e:
            sync_log.with_fields(
                event_type="session_sync_error",
                session_id=session.id,
                error=str(e)
            ).errorf("Error checking session %s in proxy: %s", session.id, e)
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
    session_log.with_fields(
        event_type="model_switch_request",
        new_model=new_model,
        api_key_id=api_key_id
    ).infof("Switching to model %s for API key %d", new_model, api_key_id)
    
    # Get current active session
    current_session = await session_crud.get_active_session_by_api_key(db, api_key_id)
    
    # Convert the new model to its ID form for comparison
    try:
        new_model_id = await model_router.get_target_model(new_model)
        session_log.with_fields(
            event_type="model_resolution",
            new_model=new_model,
            resolved_id=new_model_id
        ).infof("Resolved new model '%s' to ID: %s", new_model, new_model_id)
    except Exception as e:
        session_log.with_fields(
            event_type="model_resolution",
            new_model=new_model,
            status="error",
            error=str(e)
        ).errorf("Error resolving new model '%s' to ID: %s", new_model, e)
        # If we can't resolve the model ID, just use the original string
        new_model_id = new_model
    
    # Check if we actually need to switch models
    if current_session:
        current_model_id = current_session.model
        session_log.with_fields(
            event_type="model_comparison",
            current_model_id=current_model_id
        ).infof("Current session model ID: %s", current_model_id)
        
        # If models are the same, just return the current session
        if current_model_id == new_model_id:
            session_log.with_fields(
                event_type="model_comparison",
                current_model_id=current_model_id,
                requested_model_id=new_model_id,
                result="no_switch_needed"
            ).infof("Current model ID (%s) matches requested model ID (%s), no switch needed", current_model_id, new_model_id)
            return current_session
        
        # Models are different, close current session
        session_log.with_fields(
            event_type="model_comparison",
            current_model_id=current_model_id,
            requested_model_id=new_model_id,
            result="switch_needed"
        ).infof("Models are different. Current: %s, Requested: %s", current_model_id, new_model_id)
        session_log.with_fields(
            event_type="session_close_for_switch",
            session_id=current_session.id
        ).infof("Found existing session %s, closing before switching models", current_session.id)
        # Try closing up to 3 times
        for attempt in range(3):
            success = await close_session(db, current_session.id)
            if success:
                session_log.with_fields(
                    event_type="session_close_for_switch",
                    session_id=current_session.id,
                    attempt=attempt+1,
                    status="success"
                ).infof("Successfully closed session %s on attempt %d", current_session.id, attempt+1)
                break
            session_log.with_fields(
                event_type="session_close_for_switch",
                session_id=current_session.id,
                attempt=attempt+1,
                status="failed_retrying"
            ).warnf("Failed to close session on attempt %d, retrying...", attempt+1)
            await asyncio.sleep(1)  # Wait before retry
        
        # Verify closure with proxy router
        proxy_status = await check_proxy_session_status(current_session.id)
        if not proxy_status.get("closed", False) and proxy_status.get("exists", False):
            session_log.with_fields(
                event_type="session_close_for_switch",
                session_id=current_session.id,
                status="failed_after_retries"
            ).errorf("Failed to close session %s in proxy after multiple attempts", current_session.id)
            # Force mark as inactive in DB to prevent orphaned sessions
            await session_crud.mark_session_inactive(db, current_session.id)
    else:
        session_log.with_fields(
            event_type="model_switch_request",
            api_key_id=api_key_id,
            status="no_active_session"
        ).infof("No active session found for API key %d", api_key_id)
    
    # Create new session
    return await create_automated_session(
        db=db,
        api_key_id=api_key_id,
        user_id=user_id,
        requested_model=new_model
    ) 