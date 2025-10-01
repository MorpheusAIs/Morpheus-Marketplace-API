from fastapi import APIRouter, HTTPException, status, Query, Body, Depends, Request
from typing import Dict, Any, Optional
import httpx
import os
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
import base64
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from ....core.config import settings
from ....db.database import get_db
from ....dependencies import get_api_key_user, get_current_api_key, get_api_key_model
from ....db.models import User, Session, APIKey
from ....crud import session as session_crud
from ....crud import api_key as api_key_crud
from ....crud import private_key as private_key_crud
from ....services import proxy_router_service
from ....services import session_service
from ....core.model_routing import model_router
from ....core.logging_config import get_api_logger

logger = get_api_logger()

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

# Contract address from environment variable
DIAMOND_CONTRACT_ADDRESS = os.getenv("DIAMOND_CONTRACT_ADDRESS", "0xb8C55cD613af947E73E262F0d3C54b7211Af16CF")


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
            logger.warning("Using fallback private key for user - FOR DEBUGGING ONLY",
                          user_id=user.id,
                          event_type="fallback_key_warning")
        
        # Use SDK to make the call to the proxy-router
        try:
            response = await proxy_router_service.approveSpending(
                spender=DIAMOND_CONTRACT_ADDRESS,
                amount=amount,
                user_id=user.id,
                db=db
            )
            result = response.json()
            
            # Add note about fallback key usage
            if using_fallback and isinstance(result, dict):
                result["note"] = "Private Key not set, using fallback key (FOR DEBUGGING ONLY)"
            
            return result
            
        except proxy_router_service.ProxyRouterServiceError as e:
            logger.error("Proxy router service error in approve_spending",
                       error=str(e),
                       status_code=e.status_code,
                       error_type=e.error_type,
                       event_type="approve_spending_service_error")
            
            # Try to extract error details from the service error
            error_result = {"error": {"message": e.message, "type": e.error_type}}
            
            # Add note about fallback key usage
            if using_fallback:
                error_result["note"] = "Private Key not set, using fallback key (FOR DEBUGGING ONLY)"
            
            return error_result
    except Exception as e:
        logger.error("Unexpected error in approve_spending",
                    error=str(e),
                    event_type="approve_spending_unexpected_error")
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
    db: AsyncSession = Depends(get_db),
    api_key: APIKey = Depends(get_api_key_model)
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
    
    # Use structured logging for this endpoint
    session_logger = logger.bind(endpoint="bidsession", user_id=user.id)
    
    try:
        # First, deactivate any existing sessions for this API key
        await session_crud.deactivate_existing_sessions(db, api_key.id)
        
        # Use the proxy router to get bid details and create a session
        provider_address = None
        try:
            bid_details_url = f"{settings.PROXY_ROUTER_URL}/blockchain/bids/{bid_id}"
            session_logger.info("Fetching bid details",
                               bid_id=bid_id,
                               url=bid_details_url,
                               event_type="bid_details_fetch")
            
            bid_response = await proxy_router_service.getBidDetails(bid_id)
            
            # Extract provider address from bid details if available
            if isinstance(bid_response, dict):
                if "bid" in bid_response and "provider" in bid_response["bid"]:
                    provider_address = bid_response["bid"]["provider"]
                elif "provider" in bid_response:
                    provider_address = bid_response["provider"]
            
            if provider_address:
                session_logger.info("Found provider address in bid details",
                                   provider_address=provider_address,
                                   bid_id=bid_id)
            else:
                session_logger.warning("Could not find provider address in bid details",
                                      bid_id=bid_id,
                                      event_type="provider_address_missing")
        except Exception as e:
            session_logger.error("Error fetching bid details",
                                error=str(e),
                                bid_id=bid_id,
                                event_type="bid_details_fetch_error")
            session_logger.warning("Proceeding without provider address, may fail if required by proxy router",
                                  bid_id=bid_id)
        
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
            session_logger.error("Missing required environment variables",
                                missing_variables=missing_vars,
                                event_type="env_vars_missing")
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
        
        # Create session with proxy router
        response = await proxy_router_service.createBidSession(
            bid_id=bid_id,
            session_data=session_data_dict,
            user_id=user.id,
            db=db,
            chain_id=chain_id,
            contract_address=diamond_contract_address
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
            "api_key_prefix": api_key.key_prefix
        }
        
        # Add note about fallback key usage
        if using_fallback:
            result["note"] = "Private Key not set, using fallback key (FOR DEBUGGING ONLY)"
            
        return result
    except ValueError as e:
        session_logger.error("Error creating bid session",
                            error=str(e),
                            bid_id=bid_id,
                            event_type="bid_session_creation_error")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except proxy_router_service.ProxyRouterServiceError as e:
        session_logger.error("Proxy router error creating bid session",
                            error=str(e),
                            error_type=e.error_type,
                            bid_id=bid_id,
                            event_type="proxy_router_error")
        raise HTTPException(
            status_code=e.get_http_status_code(),
            detail=f"Error from proxy router: {str(e)}"
        )
    except Exception as e:
        session_logger.error("Unexpected error creating bid session",
                            error=str(e),
                            bid_id=bid_id,
                            event_type="bid_session_unexpected_error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )

@router.post("/modelsession")
async def create_model_session(
    model_id: str = Query(..., description="The blockchain ID (hex) of the model to create a session for"),
    session_data: SessionDataRequest = Body(..., description="Session data including duration and payment options"),
    user: User = Depends(get_api_key_user),
    api_key: APIKey = Depends(get_api_key_model),
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
    
    # Use structured logging for this endpoint  
    session_logger = logger.bind(endpoint="modelsession", user_id=user.id, model_id=model_id)
    
    try:
        # Use the new session_service.switch_model function to safely switch models
        session_logger.info("Switching to model for API key",
                           model_id=model_id,
                           api_key_id=api_key.id,
                           event_type="model_switch_start")
        
        # Override the session duration from the request
        session_duration = session_data.sessionDuration
        
        # Use our enhanced model switching function that properly ensures session cleanup
        db_session = await session_service.get_session_for_api_key(
            db=db,
            api_key_id=api_key.id,
            user_id=user.id,
            requested_model=model_id,
            session_duration=session_duration
        )
        
        if not db_session:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create new session"
            )
        
        # Verify the new session is active in both DB and proxy
        is_valid = await session_service.verify_session_status(db, db_session.id)
        if not is_valid:
            session_logger.error("Created session is not valid in proxy router",
                                session_id=db_session.id,
                                model_id=model_id,
                                event_type="session_validation_failed")
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
            "api_key_prefix": api_key.key_prefix,
            "model": model_id
        }
        
        return result
    except ValueError as e:
        session_logger.error("Error creating model session",
                            error=str(e),
                            model_id=model_id,
                            event_type="model_session_creation_error")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except proxy_router_service.ProxyRouterServiceError as e:
        session_logger.error("Proxy router error creating model session",
                            error=str(e),
                            error_type=e.error_type,
                            model_id=model_id,
                            event_type="proxy_router_error")
        raise HTTPException(
            status_code=e.get_http_status_code(),
            detail=f"Error from proxy router: {str(e)}"
        )
    except Exception as e:
        session_logger.error("Unexpected error creating model session",
                            error=str(e),
                            model_id=model_id,
                            event_type="model_session_unexpected_error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )

@router.post("/closesession")
async def close_session(
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db),
    api_key: APIKey = Depends(get_api_key_model)
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
        logger.error("Error in close_session",
                    error=str(e),
                    user_id=user.id,
                    event_type="close_session_error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error closing session: {str(e)}"
        )

@router.post("/pingsession")
async def ping_session(
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db),
    api_key: APIKey = Depends(get_api_key_model)
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
    
    ping_logger = logger.bind(endpoint="pingsession", user_id=user.id)
    
    try:            
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
        
        ping_logger.info("Testing session with chat completion",
                         session_id=session.id,
                         event_type="session_ping_test")
        
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
                ping_logger.error("Chat completion test failed",
                                 error=str(e),
                                 session_id=session.id,
                                 event_type="session_ping_failed")
                ping_logger.info("Closing dead session",
                                session_id=session.id,
                                event_type="dead_session_cleanup")
                
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
        ping_logger.error("Error in ping_session",
                         error=str(e),
                         event_type="ping_session_error")
        return {
            "status": "error",
            "message": f"Error checking session status: {str(e)}",
            "success": False
        } 