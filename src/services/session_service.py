import asyncio
import logging
import httpx
import os
import json
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta

from ..db.models import User, APIKey, UserSession
from ..core.config import settings
from ..crud import session as session_crud, private_key as private_key_crud
from .proxy_router import execute_proxy_router_operation

logger = logging.getLogger(__name__)

async def create_automated_session(
    db: AsyncSession, 
    user: User, 
    api_key: APIKey, 
    target_model: str,
    session_duration: int = 3600
) -> Optional[UserSession]:
    """
    Create an automated session for a user with the specified model.
    
    Args:
        db: Database session
        user: User object
        api_key: API key object
        target_model: Target model blockchain ID
        session_duration: Session duration in seconds
        
    Returns:
        The created UserSession object
        
    Raises:
        Exception: If session creation fails
    """
    # Log attempt to create automated session
    logger.info(f"Attempting to create automated session for user {user.id} with model {target_model}")
    
    # Validate that we have a valid model ID - it should be a hex string
    if not target_model or not target_model.startswith("0x"):
        error_msg = f"Invalid model ID format: {target_model}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    # Implement retry logic for session creation
    max_retries = 2
    retry_count = 0
    last_error = None
    
    while retry_count <= max_retries:
        try:
            # Get a private key (with possible fallback) - this matches how manual endpoint does it
            private_key, using_fallback = await private_key_crud.get_private_key_with_fallback(db, user.id)
            
            if not private_key:
                logger.error("No private key found and no fallback configured")
                raise ValueError("No private key found and no fallback key configured. Please set up your private key.")
            
            # Log private key details (but not the actual key)
            if using_fallback:
                logger.warning(f"DEBUGGING MODE: Using fallback private key for user {user.id} - this should never be used in production!")
                logger.debug(f"Fallback key length: {len(private_key)}")
                logger.debug(f"Fallback key first 6 chars: {private_key[:6]}...")
            else:
                logger.info(f"Using user's private key for user {user.id}")
                logger.debug(f"User key length: {len(private_key)}")
                logger.debug(f"User key first 6 chars: {private_key[:6]}...")
            
            # Prepare the request body with only the required parameters
            request_body = {
                "sessionDuration": session_duration,
                "directPayment": False,
                "failover": True
            }
            
            # Log the request details
            logger.info(f"Making direct request to create automated session")
            logger.debug(f"Request body: {json.dumps(request_body)}")
            
            # Make direct call to the proxy router - ENSURE CORRECT BLOCKCHAIN ENDPOINT
            full_url = f"{settings.PROXY_ROUTER_URL}/blockchain/models/{target_model}/session"
            auth = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)
            
            # Add private key header
            headers = {
                "X-Private-Key": private_key
            }
            
            # Log request details
            logger.info(f"Making request to proxy-router: {full_url}")
            logger.debug(f"Using auth user: {settings.PROXY_ROUTER_USERNAME}")
            
            # Monitor start time for request
            import time
            start_time = time.time()
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    full_url,
                    json=request_body,
                    headers=headers,
                    auth=auth,
                    timeout=30.0
                )
                
                # Log response time
                elapsed = time.time() - start_time
                logger.info(f"Response received in {elapsed:.2f} seconds")
                
                # Log response details
                logger.info(f"Response status code: {response.status_code}")
                logger.debug(f"Response headers: {dict(response.headers)}")
                
                # Force raising if there was an HTTP error
                response.raise_for_status()
                
                # Try to parse the response as JSON
                raw_text = response.text
                
                try:
                    result = response.json()
                    logger.debug(f"Response body (JSON): {json.dumps(result)}")
                except Exception as json_err:
                    logger.error(f"Could not parse response as JSON: {str(json_err)}")
                    logger.debug(f"Raw response text: {raw_text}")
                    raise ValueError(f"Invalid JSON response from proxy router: {raw_text}")
            
            # Session ID extraction - handle different response formats
            session_id = None
            
            # Check for the standard formats first
            if isinstance(result, dict):
                # Check for "sessionID" format (direct response from proxy router)
                if "sessionID" in result:
                    session_id = result["sessionID"]
                    logger.info(f"Found session ID in sessionID field: {session_id}")
                # Handle case where sessionID might have a newline character or spaces
                elif any(key.strip() == "sessionID" for key in result.keys()):
                    for key in result.keys():
                        if key.strip() == "sessionID":
                            session_id = result[key]
                            logger.info(f"Found session ID in sessionID field (after stripping): {session_id}")
                            break
                # Check for "session.id" format (older format)
                elif "session" in result and isinstance(result["session"], dict) and "id" in result["session"]:
                    session_id = result["session"]["id"]
                    logger.info(f"Found session ID in session.id field: {session_id}")
                # Check for direct "id" field
                elif "id" in result:
                    session_id = result["id"]
                    logger.info(f"Found session ID in id field: {session_id}")
            
            if not session_id:
                # Log all keys in the result to diagnose what's happening
                if isinstance(result, dict):
                    logger.error(f"Response keys: {list(result.keys())}")
                    
                error_msg = f"Session ID not found in response: {result}"
                logger.error(error_msg)
                last_error = Exception(error_msg)
                retry_count += 1
                if retry_count <= max_retries:
                    await asyncio.sleep(0.5)  # Wait before retrying
                    logger.info(f"Retrying session creation (attempt {retry_count+1})")
                continue
            
            # Create session record in database
            expires_at = datetime.utcnow() + timedelta(seconds=session_duration)
            
            # Save session to database
            try:
                logger.info(f"Attempting to save session {session_id} to database for API key {api_key.id}")
                new_session = await session_crud.create_session(
                    db=db,
                    api_key_id=api_key.id,
                    blockchain_session_id=session_id,
                    model_id=target_model,
                    expires_at=expires_at
                )
                logger.info(f"Successfully saved session {session_id} to database with internal ID {new_session.id}")
                
                # Log successful session creation
                logger.info(f"Successfully created automated session {session_id} for user {user.id}")
                
                # Add a delay to ensure the session is fully registered with the provider
                logger.info("Waiting for session to be fully registered...")
                await asyncio.sleep(2.0)  # 2 second delay
                
                # Try to verify the session exists by pinging it
                try:
                    logger.info(f"Verifying session {session_id} exists...")
                    ping_url = f"{settings.PROXY_ROUTER_URL}/blockchain/sessions/{session_id}/ping"
                    
                    ping_response = await client.post(
                        ping_url,
                        headers=headers,
                        auth=auth,
                        timeout=10.0
                    )
                    
                    if ping_response.status_code == 200:
                        logger.info(f"Session {session_id} verified successfully")
                    else:
                        logger.warning(f"Session ping returned non-200 status: {ping_response.status_code}")
                        logger.warning(f"Response: {ping_response.text}")
                except Exception as ping_err:
                    # Just log the error but continue - the session might still be usable
                    logger.warning(f"Error pinging session: {str(ping_err)}")
                
                return new_session
            except Exception as db_error:
                logger.error(f"Database error saving session: {str(db_error)}")
                logger.exception(db_error)
                raise db_error  # Re-raise to be caught by the outer try-except
            
        except httpx.HTTPStatusError as e:
            retry_count += 1
            logger.error(f"HTTP error in attempt {retry_count}: {str(e)}")
            if hasattr(e, 'response'):
                logger.error(f"Status code: {e.response.status_code}")
                logger.error(f"Response text: {e.response.text}")
            logger.exception(e)  # Log full stack trace
            last_error = e
            
            if retry_count <= max_retries:
                # Wait briefly before retrying
                await asyncio.sleep(0.5)
                logger.info(f"Retrying session creation (attempt {retry_count+1})")
            else:
                # All retries failed
                logger.error(f"All attempts to create automated session failed: {str(e)}")
                raise e
        except Exception as e:
            retry_count += 1
            logger.error(f"Attempt {retry_count} failed to create automated session: {str(e)}")
            logger.exception(e)  # Log full stack trace
            last_error = e
            
            if retry_count <= max_retries:
                # Wait briefly before retrying
                await asyncio.sleep(0.5)
                logger.info(f"Retrying session creation (attempt {retry_count+1})")
            else:
                # All retries failed
                logger.error(f"All attempts to create automated session failed: {str(e)}")
                raise e

    # If we get here, all retries failed
    if last_error:
        logger.error("Maximum retries exceeded for automated session creation")
        raise last_error
    
    # Fallback error (should never happen)
    raise Exception("Failed to create automated session for unknown reason") 