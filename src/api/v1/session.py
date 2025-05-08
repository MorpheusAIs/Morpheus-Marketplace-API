from fastapi import APIRouter, HTTPException, status, Query, Body, Depends, Request
from typing import Dict, Any, Optional
import httpx
import json
import logging
import os
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
import base64
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from ...core.config import settings
from ...db.database import get_db
from ...dependencies import get_api_key_user, get_current_api_key
from ...db.models import User, Session, APIKey
from ...crud import session as session_crud
from ...crud import api_key as api_key_crud
from ...crud import private_key as private_key_crud
from ...services.proxy_router import execute_proxy_router_operation, handle_proxy_error
from ...services import session_service
from ...core.model_routing import model_router

# Define the request models
class SessionInitRequest(BaseModel):
    network: Optional[str] = None

class SessionApproveRequest(BaseModel):
    transaction_hash: str

class SessionDataRequest(BaseModel):
    sessionDuration: int = 3600
    directPayment: bool = False
    failover: bool = False

router = APIRouter(tags=["Session"])

# Authentication credentials
AUTH = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)

# Contract address from environment variable
DIAMOND_CONTRACT_ADDRESS = os.getenv("DIAMOND_CONTRACT_ADDRESS", "0xb8C55cD613af947E73E262F0d3C54b7211Af16CF")

def handle_proxy_error(e, operation_name):
    """Common error handling for proxy router errors"""
    
    if isinstance(e, httpx.HTTPStatusError):
        logging.error(f"HTTP error during {operation_name}: {e}")
        
        # Try to extract detailed error information
        try:
            error_detail = e.response.json()
            if isinstance(error_detail, dict):
                if "error" in error_detail:
                    detail_message = error_detail["error"]
                elif "detail" in error_detail:
                    detail_message = error_detail["detail"]
                else:
                    detail_message = json.dumps(error_detail)
            else:
                detail_message = str(error_detail)
        except:
            detail_message = f"Status code: {e.response.status_code}, Reason: {e.response.reason_phrase}"
            
        return {
            "error": {
                "message": f"Error {operation_name}: {detail_message}",
                "type": "ProxyRouterError",
                "status_code": e.response.status_code
            }
        }
    else:
        # Handle other errors
        logging.error(f"Error {operation_name}: {e}")
        return {
            "error": {
                "message": f"Unexpected error {operation_name}: {str(e)}",
                "type": str(type(e).__name__),
                "details": str(e)
            }
        }

@router.post("/approve")
async def approve_spending(
    amount: int = Query(..., description="The amount to approve, consider bid price * duration for sessions"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_api_key_user)
):
    """
    Approve the contract to spend MOR tokens on your behalf.
    
    Connects to the proxy-router's /blockchain/approve endpoint.
    For creating sessions, approve enough tokens by calculating: bid_price * session_duration.
    Uses the DIAMOND_CONTRACT_ADDRESS environment variable as the spender contract address.
    """
    try:
        # Get a private key (with possible fallback)
        private_key, using_fallback = await private_key_crud.get_private_key_with_fallback(db, user.id)
        
        if not private_key:
            return {
                "error": {
                    "message": "No private key found and no fallback key configured. Please set up your private key.",
                    "type": "PrivateKeyNotFound"
                }
            }
        
        if using_fallback:
            logging.warning(f"DEBUGGING MODE: Using fallback private key for user {user.id} - this should never be used in production!")
        
        # Now make the direct call to the proxy-router
        full_url = f"{settings.PROXY_ROUTER_URL}/blockchain/approve"
        auth = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)
        headers = {"X-Private-Key": private_key}
        params = {"spender": DIAMOND_CONTRACT_ADDRESS, "amount": amount}
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    full_url,
                    params=params,
                    headers=headers,
                    auth=auth,
                    timeout=30.0
                )
                response.raise_for_status()
                result = response.json()
                
                # Add note about fallback key usage
                if using_fallback and isinstance(result, dict):
                    result["note"] = "Private Key not set, using fallback key (FOR DEBUGGING ONLY)"
                
                return result
            except httpx.HTTPStatusError as http_err:
                logging.error(f"HTTP error in approve_spending: {http_err}")
                try:
                    error_data = http_err.response.json()
                    error_result = {"error": error_data}
                    
                    # Add note about fallback key usage
                    if using_fallback:
                        error_result["note"] = "Private Key not set, using fallback key (FOR DEBUGGING ONLY)"
                    
                    return error_result
                except:
                    error_msg = f"HTTP error: {http_err.response.status_code} - {http_err.response.reason_phrase}"
                    if using_fallback:
                        error_msg = f"[USING FALLBACK KEY] {error_msg}"
                    
                    return {
                        "error": {
                            "message": error_msg,
                            "type": "HTTPError"
                        }
                    }
            except Exception as req_err:
                logging.error(f"Request error in approve_spending: {req_err}")
                error_msg = str(req_err)
                if using_fallback:
                    error_msg = f"[USING FALLBACK KEY] {error_msg}"
                
                return {
                    "error": {
                        "message": error_msg,
                        "type": str(type(req_err).__name__)
                    }
                }
    except Exception as e:
        logging.error(f"Unexpected error in approve_spending: {e}")
        return {
            "error": {
                "message": str(e),
                "type": str(type(e).__name__)
            }
        }

@router.post("/bidsession")
async def create_bid_session(
    bid_id: str = Query(..., description="The blockchain ID (hex) of the bid to create a session for"),
    session_data: SessionDataRequest = Body(..., description="Session data including duration and payment options"),
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a session with a provider using a bid ID and associate it with the API key.
    
    This endpoint creates a session and automatically associates it with the API key used for authentication.
    Each API key can have at most one active session at a time.
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Setup detailed logging
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger("bidsession")
    
    # We need to extract the API key prefix, but we know it's already loaded
    # Using the API key returned from the dependency is safer than depending on user.api_keys
    api_key_prefix = user.api_keys[0].key_prefix if user.api_keys and len(user.api_keys) > 0 else None
    if not api_key_prefix:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No API key found for this user"
        )
    
    # Since user.api_keys is already loaded by the dependency, we can directly get the first API key
    # without another database query
    api_key = user.api_keys[0] if user.api_keys and len(user.api_keys) > 0 else None
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API key not found"
        )
    
    try:
        # First, deactivate any existing sessions for this API key
        await session_crud.deactivate_existing_sessions(db, api_key.id)
        
        # Use the proxy router to get bid details and create a session
        provider_address = None
        try:
            bid_details_url = f"{settings.PROXY_ROUTER_URL}/blockchain/bids/{bid_id}"
            logger.info(f"Fetching bid details from: {bid_details_url}")
            
            bid_response = await execute_proxy_router_operation(
                "GET",
                f"blockchain/bids/{bid_id}",
                max_retries=2
            )
            
            # Extract provider address from bid details if available
            if isinstance(bid_response, dict):
                if "bid" in bid_response and "provider" in bid_response["bid"]:
                    provider_address = bid_response["bid"]["provider"]
                elif "provider" in bid_response:
                    provider_address = bid_response["provider"]
            
            if provider_address:
                logger.info(f"Found provider address in bid details: {provider_address}")
            else:
                logger.warning("Could not find provider address in bid details")
        except Exception as e:
            logger.error(f"Error fetching bid details: {str(e)}")
            logger.warning("Proceeding without provider address, may fail if required by proxy router")
        
        # Get required environment variables
        chain_id = os.getenv("CHAIN_ID")
        diamond_contract_address = os.getenv("DIAMOND_CONTRACT_ADDRESS")
        contract_address = os.getenv("CONTRACT_ADDRESS")
        
        # Check for required environment variables
        missing_vars = []
        if not chain_id:
            missing_vars.append("CHAIN_ID")
        if not diamond_contract_address:
            missing_vars.append("DIAMOND_CONTRACT_ADDRESS")
        if not contract_address:
            missing_vars.append("CONTRACT_ADDRESS")
            
        if missing_vars:
            error_msg = f"Missing required environment variables: {', '.join(missing_vars)}"
            logger.error(error_msg)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_msg
            )
        
        # Prepare session data
        session_data_dict = {
            "sessionDuration": session_data.sessionDuration,
            "failover": session_data.failover,
            "directPayment": session_data.directPayment
        }
        
        # Add provider information if available
        if provider_address:
            session_data_dict["provider"] = provider_address
            
        # Add chain_id and contract addresses
        try:
            session_data_dict["chainId"] = int(chain_id)
        except ValueError:
            session_data_dict["chainId"] = chain_id
            
        session_data_dict["modelContract"] = contract_address
        session_data_dict["diamondContract"] = diamond_contract_address
        
        # Get a private key (with possible fallback)
        private_key, using_fallback = await private_key_crud.get_private_key_with_fallback(db, user.id)
        
        if not private_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No private key found and no fallback key configured"
            )
        
        # Add private key to headers
        headers = {
            "X-Private-Key": private_key,
            "X-Chain-ID": chain_id,
            "X-Contract-Address": diamond_contract_address
        }
        
        # Create session with proxy router
        response = await execute_proxy_router_operation(
            "POST",
            f"blockchain/bids/{bid_id}/session",
            headers=headers,
            json=session_data_dict,
            max_retries=3
        )
        
        # Extract session ID from response
        blockchain_session_id = None
        
        if isinstance(response, dict):
            blockchain_session_id = (response.get("sessionID") or 
                                   response.get("session", {}).get("id") or 
                                   response.get("id"))
        
        if not blockchain_session_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid session response from proxy-router - no session ID found"
            )
        
        # Store session in database using the new session model
        expiry_time_with_tz = datetime.now(timezone.utc) + timedelta(seconds=session_data.sessionDuration)
        # Convert to naive datetime for DB compatibility
        expiry_time = expiry_time_with_tz.replace(tzinfo=None)
        db_session = await session_crud.create_session(
            db=db,
            session_id=blockchain_session_id,
            api_key_id=api_key.id,
            user_id=user.id,
            model=bid_id,
            session_type="bid",
            expires_at=expiry_time
        )
        
        # Return success response
        result = {
            "success": True,
            "message": "Session created and associated with API key",
            "session_id": blockchain_session_id,
            "api_key_prefix": api_key_prefix
        }
        
        # Add note about fallback key usage
        if using_fallback:
            result["note"] = "Private Key not set, using fallback key (FOR DEBUGGING ONLY)"
            
        return result
    except ValueError as e:
        logger.error(f"Error creating bid session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error creating bid session: {str(e)}")
        raise HTTPException(
            status_code=e.response.status_code if hasattr(e, 'response') else 500,
            detail=f"Error from proxy router: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Unexpected error creating bid session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )

@router.post("/modelsession")
async def create_model_session(
    model_id: str = Query(..., description="The blockchain ID (hex) of the model to create a session for"),
    session_data: SessionDataRequest = Body(..., description="Session data including duration and payment options"),
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a session with a provider using a model ID and associate it with the API key.
    
    This endpoint creates a session and automatically associates it with the API key used for authentication.
    Each API key can have at most one active session at a time.
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Setup detailed logging
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger("modelsession")
    
    # We need to extract the API key prefix, but we know it's already loaded
    # Using the API key returned from the dependency is safer than depending on user.api_keys
    api_key_prefix = user.api_keys[0].key_prefix if user.api_keys and len(user.api_keys) > 0 else None
    if not api_key_prefix:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No API key found for this user"
        )
    
    # Since user.api_keys is already loaded by the dependency, we can directly get the first API key
    # without another database query
    api_key = user.api_keys[0] if user.api_keys and len(user.api_keys) > 0 else None
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API key not found"
        )
    
    try:
        # Use the new session_service.switch_model function to safely switch models
        logger.info(f"Switching to model {model_id} for API key {api_key.id}")
        
        # Override the session duration from the request
        session_duration = session_data.sessionDuration
        
        # Use our enhanced model switching function that properly ensures session cleanup
        db_session = await session_service.switch_model(
            db=db,
            api_key_id=api_key.id,
            user_id=user.id,
            new_model=model_id
        )
        
        if not db_session:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create new session"
            )
        
        # Verify the new session is active in both DB and proxy
        is_valid = await session_service.verify_session_status(db, db_session.id)
        if not is_valid:
            logger.error(f"Created session {db_session.id} is not valid in proxy router")
            # Try to close it cleanly
            await session_service.close_session(db, db_session.id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Session created but not valid in proxy router"
            )
            
        # Return success response
        result = {
            "success": True,
            "message": "Session created and associated with API key",
            "session_id": db_session.id,
            "api_key_prefix": api_key_prefix,
            "model": model_id
        }
        
        return result
    except ValueError as e:
        logger.error(f"Error creating model session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error creating model session: {str(e)}")
        raise HTTPException(
            status_code=e.response.status_code if hasattr(e, 'response') else 500,
            detail=f"Error from proxy router: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Unexpected error creating model session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )

@router.post("/closesession")
async def close_session(
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Close the session associated with the current API key.
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        # We need to extract the API key prefix, but we know it's already loaded
        api_key_prefix = user.api_keys[0].key_prefix if user.api_keys and len(user.api_keys) > 0 else None
        if not api_key_prefix:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No API key found for this user"
            )
        
        # Since user.api_keys is already loaded by the dependency, we can directly get the first API key
        api_key = user.api_keys[0] if user.api_keys and len(user.api_keys) > 0 else None
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="API key not found"
            )
        
        # Get session associated with the API key using the new CRUD function
        session = await session_crud.get_active_session_by_api_key(db, api_key.id)
        
        if not session:
            return {
                "success": True,
                "message": "No active session found to close",
                "session_id": None
            }
        
        if session.is_expired:
            # Just mark as inactive if already expired
            await session_crud.mark_session_inactive(db, session.id)
            return {
                "success": True,
                "message": "Expired session marked as inactive",
                "session_id": session.id
            }
        
        # Use the session service to close the session
        success = await session_service.close_session(db, session.id)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to close session"
            )
        
        return {
            "success": True,
            "message": "Session closed successfully",
            "session_id": session.id
        }
    except Exception as e:
        logging.error(f"Error in close_session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error closing session: {str(e)}"
        )

@router.post("/pingsession")
async def ping_session(
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Ping the session by attempting a simple chat completion.
    If the chat completion fails, the session is considered dead and will be closed.
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    logger = logging.getLogger(__name__)
    
    try:
        # Get API key
        api_key = user.api_keys[0] if user.api_keys and len(user.api_keys) > 0 else None
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="API key not found"
            )
            
        # Get session associated with the API key using the new CRUD function
        session = await session_crud.get_active_session_by_api_key(db, api_key.id)
        
        if not session:
            return {
                "status": "no_session",
                "message": "No active session found for this API key",
                "success": False
            }
        
        if session.is_expired:
            # If session is expired, mark it as inactive
            await session_crud.mark_session_inactive(db, session.id)
            return {
                "status": "expired",
                "message": "Session is expired and has been marked as inactive",
                "session_id": session.id,
                "success": False
            }
        
        # Prepare a simple chat completion request
        test_message = {
            "messages": [{"role": "user", "content": "test"}],
            "stream": True  # Always use streaming as that's what the proxy router expects
        }
        
        # Create basic auth header
        auth_str = f"{settings.PROXY_ROUTER_USERNAME}:{settings.PROXY_ROUTER_PASSWORD}"
        auth_b64 = base64.b64encode(auth_str.encode('ascii')).decode('ascii')
        
        # Setup headers for chat completion - match exactly what the chat endpoint uses
        headers = {
            "authorization": f"Basic {auth_b64}",
            "Content-Type": "application/json",
            "accept": "text/event-stream",
            "session_id": session.id
        }
        
        # Make request to chat completions endpoint
        endpoint = f"{settings.PROXY_ROUTER_URL}/v1/chat/completions"
        
        logger.info(f"Testing session {session.id} with chat completion")
        
        async with httpx.AsyncClient() as client:
            try:
                # Use streaming request like the chat endpoint
                async with client.stream(
                    "POST",
                    endpoint,
                    json=test_message,
                    headers=headers,
                    timeout=30.0
                ) as response:
                    response.raise_for_status()
                    
                    # Read just enough of the stream to confirm it's working
                    async for chunk in response.aiter_bytes():
                        # If we get any response chunk, the session is alive
                        return {
                            "status": "alive",
                            "message": "Session is alive",
                            "session_id": session.id,
                            "success": True
                        }
                    
                    # If we get here with no chunks, something is wrong
                    raise Exception("No response received from chat completion")
                    
            except Exception as e:
                logger.error(f"Chat completion test failed: {str(e)}")
                logger.info(f"Closing dead session {session.id}")
                
                # Session is dead, close it using the session service
                await session_service.close_session(db, session.id)
                
                return {
                    "status": "dead",
                    "message": "Session is dead and has been closed",
                    "session_id": session.id,
                    "success": False,
                    "error": str(e)
                }
                
    except Exception as e:
        logger.error(f"Error in ping_session: {str(e)}")
        return {
            "status": "error",
            "message": f"Error checking session status: {str(e)}",
            "success": False
        } 