from fastapi import APIRouter, HTTPException, status, Query, Body, Depends, Request
from typing import Dict, Any, Optional
import httpx
import json
import logging
import os
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
import base64

from ...core.config import settings
from ...db.database import get_db
from ...dependencies import get_api_key_user
from ...db.models import User
from ...crud import session as session_crud
from ...crud import api_key as api_key_crud
from ...crud import private_key as private_key_crud
from ...services.proxy_router import execute_proxy_router_operation, handle_proxy_error

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
    
    # Check if there's already an active session
    existing_session = await session_crud.get_session_by_api_key_id(db, api_key.id)
    if existing_session and existing_session.is_active:
        # Close the existing session
        try:
            # Try to close session but don't raise an error if it fails
            try:
                # Use our updated utility function that supports fallback private key
                await execute_proxy_router_operation(
                    endpoint=f"blockchain/sessions/{existing_session.session_id}/close",
                    user_id=user.id,
                    db=db
                )
            except Exception as e:
                # Log the error but continue with creating a new session
                logger.warning(f"Failed to close existing session: {str(e)}")
            
            # Mark the session as inactive in the database
            await session_crud.update_session_status(db, existing_session.id, False)
        except Exception as close_err:
            logger.error(f"Error updating session status: {close_err}")
            # Continue despite error - we'll create a new session anyway
    
    # Get environment variables needed for blockchain operations
    chain_id = os.getenv("CHAIN_ID")
    diamond_contract_address = os.getenv("DIAMOND_CONTRACT_ADDRESS")
    contract_address = os.getenv("CONTRACT_ADDRESS")
    
    # Log environment variable details
    logger.debug(f"PROXY_ROUTER_URL: {settings.PROXY_ROUTER_URL}")
    logger.debug(f"PROXY_ROUTER_USERNAME: {settings.PROXY_ROUTER_USERNAME}")
    logger.debug(f"CHAIN_ID: {chain_id}")
    logger.debug(f"DIAMOND_CONTRACT_ADDRESS: {diamond_contract_address}")
    logger.debug(f"CONTRACT_ADDRESS: {contract_address}")
    
    # Setup auth for proxy router
    auth = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)
    
    # Try to fetch bid details to get provider address
    provider_address = None
    try:
        # Build the URL to get bid details
        bid_details_url = f"{settings.PROXY_ROUTER_URL}/blockchain/bids/{bid_id}"
        logger.info(f"Fetching bid details from: {bid_details_url}")
        
        # Make the request to get bid details
        async with httpx.AsyncClient() as client:
            bid_response = await client.get(
                bid_details_url,
                auth=auth,
                timeout=10.0
            )
            bid_response.raise_for_status()
            bid_details = bid_response.json()
            
            # Log the bid details (excluding any sensitive information)
            logger.debug(f"Bid details: {json.dumps(bid_details)}")
            
            # Extract provider address from bid details if available
            if isinstance(bid_details, dict):
                # The structure depends on the proxy router API, adjust as needed
                if "bid" in bid_details and "provider" in bid_details["bid"]:
                    provider_address = bid_details["bid"]["provider"]
                elif "provider" in bid_details:
                    provider_address = bid_details["provider"]
            
            if provider_address:
                logger.info(f"Found provider address in bid details: {provider_address}")
            else:
                logger.warning("Could not find provider address in bid details")
            
    except Exception as e:
        logger.error(f"Error fetching bid details: {str(e)}")
        logger.warning("Proceeding without provider address, may fail if required by proxy router")
    
    # Create the session with the bid using our updated utility function with fallback
    try:
        # Make direct call to avoid nested async issues
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        
        # Get a private key (with possible fallback)
        private_key, using_fallback = await private_key_crud.get_private_key_with_fallback(db, user.id)
        
        if not private_key:
            logger.error("No private key found and no fallback configured")
            return {
                "error": {
                    "message": "No private key found and no fallback key configured. Please set up your private key.",
                    "type": "PrivateKeyNotFound"
                }
            }
        
        # Log private key details (but not the actual key)
        if using_fallback:
            logger.warning(f"DEBUGGING MODE: Using fallback private key for user {user.id} - this should never be used in production!")
            logger.debug(f"Fallback key length: {len(private_key)}")
            logger.debug(f"Fallback key first 6 chars: {private_key[:6]}...")
        else:
            logger.info(f"Using user's private key for user {user.id}")
            logger.debug(f"User key length: {len(private_key)}")
            logger.debug(f"User key first 6 chars: {private_key[:6]}...")
        
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
            return {
                "error": {
                    "message": error_msg,
                    "type": "ConfigurationError",
                    "missing_vars": missing_vars
                }
            }
        
        # Add required parameters to the session data if not present
        if not isinstance(session_data, dict):
            session_data = {"sessionDuration": 3600}
            
        # Add required parameters to the session data if not present
        if "sessionDuration" not in session_data:
            session_data["sessionDuration"] = 3600  # Default to 1 hour
            
        # Add required flags to the session data if not present (using same defaults as the direct call)
        if "failover" not in session_data:
            session_data["failover"] = False
            
        if "directPayment" not in session_data:
            session_data["directPayment"] = False
            
        # Add provider information if not present
        if "provider" not in session_data and provider_address:
            logger.info(f"Adding provider address from bid details: {provider_address}")
            session_data["provider"] = provider_address
            
        # Add chain_id to the session data if not present
        if "chainId" not in session_data and chain_id:
            try:
                session_data["chainId"] = int(chain_id)
            except ValueError:
                logger.warning(f"Could not convert chain_id '{chain_id}' to integer, using as string")
                session_data["chainId"] = chain_id
            
        # Add contract addresses to the session data
        if "modelContract" not in session_data and contract_address:
            session_data["modelContract"] = contract_address
            
        if "diamondContract" not in session_data and diamond_contract_address:
            session_data["diamondContract"] = diamond_contract_address
            
        # Log the final session data
        logger.debug(f"Final session data: {json.dumps(session_data)}")
        
        # Now make direct call to the proxy-router
        full_url = f"{settings.PROXY_ROUTER_URL}/blockchain/bids/{bid_id}/session"
        auth = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)
        
        # Add chain ID header which may be needed by the proxy router
        headers = {
            "X-Private-Key": private_key,
            "X-Chain-ID": chain_id,
            "X-Contract-Address": diamond_contract_address
        }
        
        # Log request details
        logger.info(f"Making request to proxy-router: {full_url}")
        logger.debug(f"Request body: {json.dumps(session_data)}")
        logger.debug(f"Using auth user: {settings.PROXY_ROUTER_USERNAME}")
        logger.debug(f"Headers: {json.dumps({k: ('***' if k == 'X-Private-Key' else v) for k, v in headers.items()})}")
        
        # Monitor start time for request
        import time
        start_time = time.time()
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    full_url,
                    json=session_data,
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
                
                # Try to parse the response as JSON
                raw_text = response.text
                
                try:
                    response_json = response.json()
                    logger.debug(f"Response body (JSON): {json.dumps(response_json)}")
                except Exception as json_err:
                    logger.error(f"Could not parse response as JSON: {str(json_err)}")
                    logger.debug(f"Raw response text: {raw_text}")
                    
                    # Try to clean the response and parse it again
                    try:
                        # Clean the raw response text
                        cleaned_text = raw_text.strip().replace('\n', '')
                        response_json = json.loads(cleaned_text)
                        logger.info(f"Successfully parsed JSON after cleaning")
                    except Exception as clean_err:
                        logger.error(f"Could not parse response as JSON even after cleaning: {str(clean_err)}")
                        # If we can't parse as JSON, create a error response
                        return {
                            "error": {
                                "message": f"Proxy router returned non-JSON response: {raw_text[:200]}...",
                                "type": "InvalidResponseFormat",
                                "raw_response": raw_text[:500] if len(raw_text) > 500 else raw_text
                            }
                        }
                
                # Inspect response for error information
                if 400 <= response.status_code < 600:
                    error_detail = "Unknown error"
                    if isinstance(response_json, dict):
                        if "error" in response_json:
                            error_detail = response_json["error"]
                        elif "detail" in response_json:
                            error_detail = response_json["detail"]
                        elif "message" in response_json:
                            error_detail = response_json["message"]
                    
                    logger.error(f"Proxy router error response: {error_detail}")
                    return {
                        "error": {
                            "message": f"Proxy router error: {error_detail}",
                            "type": "ProxyRouterError",
                            "status_code": response.status_code,
                            "response": response_json
                        }
                    }
                
                # Force raising if there was an HTTP error
                response.raise_for_status()
                
                session_response = response_json
                
                # Extra validation for the response format
                if not isinstance(session_response, dict):
                    error_msg = f"Unexpected response format from proxy-router: {type(session_response)}"
                    logger.error(error_msg)
                    return {
                        "error": {
                            "message": error_msg,
                            "type": "InvalidResponseFormat",
                            "response": session_response
                        }
                    }
                
                # Log the session ID if we can find it
                session_id = session_response.get("session", {}).get("id")
                if session_id:
                    logger.info(f"Successfully created session with ID: {session_id}")
                else:
                    logger.warning("Session ID not found in response")
                    logger.debug(f"Response keys: {list(session_response.keys())}")
                    if "session" in session_response:
                        logger.debug(f"Session keys: {list(session_response['session'].keys())}")
                    else:
                        logger.debug(f"Complete response: {json.dumps(session_response)}")
            
            except httpx.HTTPError as http_err:
                logger.error(f"HTTP error: {str(http_err)}")
                if hasattr(http_err, 'response'):
                    logger.error(f"Response status: {http_err.response.status_code}")
                    logger.error(f"Response body: {http_err.response.text}")
                
                return {
                    "error": {
                        "message": f"HTTP error communicating with proxy router: {str(http_err)}",
                        "type": "HTTPError",
                        "status_code": http_err.response.status_code if hasattr(http_err, 'response') else 500,
                        "response": http_err.response.text if hasattr(http_err, 'response') else "No response"
                    }
                }
        
        # Check for a valid session ID in the response - handle different response formats
        blockchain_session_id = None
        
        # First check common response formats 
        if isinstance(session_response, dict):
            # Check for "sessionID" format (direct response from proxy router)
            if "sessionID" in session_response:
                blockchain_session_id = session_response["sessionID"]
                logger.info(f"Found session ID in sessionID field: {blockchain_session_id}")
            # Handle case where sessionID might have a newline character or spaces
            elif any(key.strip() == "sessionID" for key in session_response.keys()):
                for key in session_response.keys():
                    if key.strip() == "sessionID":
                        blockchain_session_id = session_response[key]
                        logger.info(f"Found session ID in sessionID field (after stripping): {blockchain_session_id}")
                        break
            # Check for "session.id" format (older format)
            elif "session" in session_response and isinstance(session_response["session"], dict) and "id" in session_response["session"]:
                blockchain_session_id = session_response["session"]["id"]
                logger.info(f"Found session ID in session.id field: {blockchain_session_id}")
            # Check for direct "id" field
            elif "id" in session_response:
                blockchain_session_id = session_response["id"]
                logger.info(f"Found session ID in id field: {blockchain_session_id}")
        
        if not blockchain_session_id:
            error_msg = "Invalid session response from proxy-router - no session ID found"
            logger.error(error_msg)
            logger.error(f"Complete response: {json.dumps(session_response)}")
            
            # Instead of raising an exception, return a meaningful error response
            return {
                "error": {
                    "message": error_msg,
                    "type": "InvalidResponseFormat",
                    "response": session_response
                }
            }
        
        # Store session information in the database
        session_duration = session_data.get("sessionDuration", 3600)
        db_session = await session_crud.create_session(
            db, 
            api_key.id, 
            blockchain_session_id, 
            bid_id,
            session_duration
        )
        
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
    except Exception as e:
        logger.error(f"Error creating bid session: {str(e)}")
        
        if isinstance(e, httpx.HTTPStatusError):
            logger.error(f"HTTP Status code: {e.response.status_code}")
            logger.error(f"Response text: {e.response.text}")
            
            # Try to parse error response
            try:
                error_json = e.response.json()
                return {
                    "error": {
                        "message": f"Proxy router error: {str(e)}",
                        "type": "ProxyRouterError",
                        "status_code": e.response.status_code,
                        "response": error_json
                    }
                }
            except:
                pass
        
        # Default to the standard error handler
        return handle_proxy_error(e, "creating bid session")

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
    
    # Check if there's already an active session
    existing_session = await session_crud.get_session_by_api_key_id(db, api_key.id)
    if existing_session and existing_session.is_active:
        # Close the existing session
        try:
            # Try to close session but don't raise an error if it fails
            try:
                # Use our updated utility function that supports fallback private key
                await execute_proxy_router_operation(
                    endpoint=f"blockchain/sessions/{existing_session.session_id}/close",
                    user_id=user.id,
                    db=db
                )
            except Exception as e:
                # Log the error but continue with creating a new session
                logger.warning(f"Failed to close existing session: {str(e)}")
            
            # Mark the session as inactive in the database
            await session_crud.update_session_status(db, existing_session.id, False)
        except Exception as close_err:
            logger.error(f"Error updating session status: {close_err}")
            # Continue despite error - we'll create a new session anyway
    
    # Create the session with the model using our updated utility function with fallback
    try:
        # Make direct call to avoid nested async issues
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        
        # Get a private key (with possible fallback)
        private_key, using_fallback = await private_key_crud.get_private_key_with_fallback(db, user.id)
        
        if not private_key:
            logger.error("No private key found and no fallback configured")
            return {
                "error": {
                    "message": "No private key found and no fallback key configured. Please set up your private key.",
                    "type": "PrivateKeyNotFound"
                }
            }
        
        # Log private key details (but not the actual key)
        if using_fallback:
            logger.warning(f"DEBUGGING MODE: Using fallback private key for user {user.id} - this should never be used in production!")
            logger.debug(f"Fallback key length: {len(private_key)}")
            logger.debug(f"Fallback key first 6 chars: {private_key[:6]}...")
        else:
            logger.info(f"Using user's private key for user {user.id}")
            logger.debug(f"User key length: {len(private_key)}")
            logger.debug(f"User key first 6 chars: {private_key[:6]}...")
        
        # Check proxy router settings
        logger.debug(f"PROXY_ROUTER_URL: {settings.PROXY_ROUTER_URL}")
        logger.debug(f"PROXY_ROUTER_USERNAME: {settings.PROXY_ROUTER_USERNAME}")
        
        # Prepare the request body with only the required parameters
        request_body = {
            "sessionDuration": session_data.sessionDuration,
            "directPayment": session_data.directPayment,
            "failover": session_data.failover
        }
        
        # Log the final request body
        logger.debug(f"Final request body: {json.dumps(request_body)}")
        
        # Now make direct call to the proxy-router
        full_url = f"{settings.PROXY_ROUTER_URL}/blockchain/models/{model_id}/session"
        auth = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)
        
        # Add private key header
        headers = {
            "X-Private-Key": private_key
        }
        
        # Log request details
        logger.info(f"Making request to proxy-router: {full_url}")
        logger.debug(f"Request body: {json.dumps(request_body)}")
        logger.debug(f"Using auth user: {settings.PROXY_ROUTER_USERNAME}")
        logger.debug(f"Headers: {json.dumps({k: ('***' if k == 'X-Private-Key' else v) for k, v in headers.items()})}")
        
        # Monitor start time for request
        import time
        start_time = time.time()
        
        async with httpx.AsyncClient() as client:
            try:
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
                
                # Try to parse the response as JSON
                raw_text = response.text
                
                try:
                    response_json = response.json()
                    logger.debug(f"Response body (JSON): {json.dumps(response_json)}")
                except Exception as json_err:
                    logger.error(f"Could not parse response as JSON: {str(json_err)}")
                    logger.debug(f"Raw response text: {raw_text}")
                    
                    # Try to clean the response and parse it again
                    try:
                        # Clean the raw response text
                        cleaned_text = raw_text.strip().replace('\n', '')
                        response_json = json.loads(cleaned_text)
                        logger.info(f"Successfully parsed JSON after cleaning")
                    except Exception as clean_err:
                        logger.error(f"Could not parse response as JSON even after cleaning: {str(clean_err)}")
                        # If we can't parse as JSON, create a error response
                        return {
                            "error": {
                                "message": f"Proxy router returned non-JSON response: {raw_text[:200]}...",
                                "type": "InvalidResponseFormat",
                                "raw_response": raw_text[:500] if len(raw_text) > 500 else raw_text
                            }
                        }
                
                # Inspect response for error information
                if 400 <= response.status_code < 600:
                    error_detail = "Unknown error"
                    if isinstance(response_json, dict):
                        if "error" in response_json:
                            error_detail = response_json["error"]
                        elif "detail" in response_json:
                            error_detail = response_json["detail"]
                        elif "message" in response_json:
                            error_detail = response_json["message"]
                    
                    logger.error(f"Proxy router error response: {error_detail}")
                    return {
                        "error": {
                            "message": f"Proxy router error: {error_detail}",
                            "type": "ProxyRouterError",
                            "status_code": response.status_code,
                            "response": response_json
                        }
                    }
                
                # Force raising if there was an HTTP error
                response.raise_for_status()
                
                session_response = response_json
                
                # Extra validation for the response format
                if not isinstance(session_response, dict):
                    error_msg = f"Unexpected response format from proxy-router: {type(session_response)}"
                    logger.error(error_msg)
                    return {
                        "error": {
                            "message": error_msg,
                            "type": "InvalidResponseFormat",
                            "response": session_response
                        }
                    }
                
                # Log the session ID if we can find it
                session_id = session_response.get("session", {}).get("id")
                if session_id:
                    logger.info(f"Successfully created session with ID: {session_id}")
                else:
                    logger.warning("Session ID not found in response")
                    logger.debug(f"Response keys: {list(session_response.keys())}")
                    if "session" in session_response:
                        logger.debug(f"Session keys: {list(session_response['session'].keys())}")
                    else:
                        logger.debug(f"Complete response: {json.dumps(session_response)}")
            
            except httpx.HTTPError as http_err:
                logger.error(f"HTTP error: {str(http_err)}")
                if hasattr(http_err, 'response'):
                    logger.error(f"Response status: {http_err.response.status_code}")
                    logger.error(f"Response body: {http_err.response.text}")
                
                return {
                    "error": {
                        "message": f"HTTP error communicating with proxy router: {str(http_err)}",
                        "type": "HTTPError",
                        "status_code": http_err.response.status_code if hasattr(http_err, 'response') else 500,
                        "response": http_err.response.text if hasattr(http_err, 'response') else "No response"
                    }
                }
            
            # Check for a valid session ID in the response - handle different response formats
            blockchain_session_id = None
            
            # First check common response formats 
            if isinstance(session_response, dict):
                # Check for "sessionID" format (direct response from proxy router)
                if "sessionID" in session_response:
                    blockchain_session_id = session_response["sessionID"]
                    logger.info(f"Found session ID in sessionID field: {blockchain_session_id}")
                # Handle case where sessionID might have a newline character or spaces
                elif any(key.strip() == "sessionID" for key in session_response.keys()):
                    for key in session_response.keys():
                        if key.strip() == "sessionID":
                            blockchain_session_id = session_response[key]
                            logger.info(f"Found session ID in sessionID field (after stripping): {blockchain_session_id}")
                            break
                # Check for "session.id" format (older format)
                elif "session" in session_response and isinstance(session_response["session"], dict) and "id" in session_response["session"]:
                    blockchain_session_id = session_response["session"]["id"]
                    logger.info(f"Found session ID in session.id field: {blockchain_session_id}")
                # Check for direct "id" field
                elif "id" in session_response:
                    blockchain_session_id = session_response["id"]
                    logger.info(f"Found session ID in id field: {blockchain_session_id}")
            
            if not blockchain_session_id:
                error_msg = "Invalid session response from proxy-router - no session ID found"
                logger.error(error_msg)
                logger.error(f"Complete response: {json.dumps(session_response)}")
                
                # Instead of raising an exception, return a meaningful error response
                return {
                    "error": {
                        "message": error_msg,
                        "type": "InvalidResponseFormat",
                        "response": session_response
                    }
                }
            
            # Store session information in the database
            session_duration = session_data.sessionDuration
            db_session = await session_crud.create_session(
                db, 
                api_key.id, 
                blockchain_session_id, 
                model_id,
                session_duration
            )
                
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
                
    except Exception as e:
        logger.error(f"Error creating model session: {str(e)}")
        
        if isinstance(e, httpx.HTTPStatusError):
            logger.error(f"HTTP Status code: {e.response.status_code}")
            logger.error(f"Response text: {e.response.text}")
            
            # Try to parse error response
            try:
                error_json = e.response.json()
                return {
                    "error": {
                        "message": f"Proxy router error: {str(e)}",
                        "type": "ProxyRouterError",
                        "status_code": e.response.status_code,
                        "response": error_json
                    }
                }
            except:
                pass
        
        # Default to the standard error handler
        return handle_proxy_error(e, "creating model session")

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
        
        # Get session associated with the API key
        session = await session_crud.get_session_by_api_key_id(db, api_key.id)
        
        if not session or not session.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active session found for this API key"
            )
        
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
        
        # Make direct call to the proxy-router
        full_url = f"{settings.PROXY_ROUTER_URL}/blockchain/sessions/{session.session_id}/close"
        auth = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)
        headers = {"X-Private-Key": private_key}
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                full_url,
                headers=headers,
                auth=auth,
                timeout=30.0
            )
            response.raise_for_status()
            close_response = response.json()
        
        # Mark the session as inactive in the database
        await session_crud.update_session_status(db, session.id, False)
        
        result = {
            "success": True,
            "message": "Session closed successfully",
            "session_id": session.session_id
        }
        
        # Add note about fallback key usage
        if using_fallback:
            result["note"] = "Private Key not set, using fallback key (FOR DEBUGGING ONLY)"
            
        return result
    except Exception as e:
        return handle_proxy_error(e, "closing session")

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
            
        # Get session associated with the API key
        session = await session_crud.get_session_by_api_key_id(db, api_key.id)
        
        if not session or not session.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active session found for this API key"
            )
        
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
            "session_id": session.session_id
        }
        
        # Make request to chat completions endpoint
        endpoint = f"{settings.PROXY_ROUTER_URL}/v1/chat/completions"
        
        logger.info(f"Testing session {session.session_id} with chat completion")
        
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
                            "success": True,
                            "message": "Session is alive",
                            "session_id": session.session_id
                        }
                    
                    # If we get here with no chunks, something is wrong
                    raise Exception("No response received from chat completion")
                    
            except Exception as e:
                logger.error(f"Chat completion test failed: {str(e)}")
                logger.info(f"Closing dead session {session.session_id}")
                
                # Session is dead, try to close it
                try:
                    # Get a private key (with possible fallback)
                    private_key, using_fallback = await private_key_crud.get_private_key_with_fallback(db, user.id)
                    
                    if private_key:
                        # Setup headers for closing session
                        close_headers = {
                            "X-Private-Key": private_key,
                            "X-Chain-ID": os.getenv("CHAIN_ID"),
                            "X-Contract-Address": os.getenv("DIAMOND_CONTRACT_ADDRESS")
                        }
                        
                        # Try to close the session on the blockchain
                        await execute_proxy_router_operation(
                            endpoint=f"blockchain/sessions/{session.session_id}/close",
                            headers=close_headers,
                            user_id=user.id,
                            db=db
                        )
                except Exception as close_err:
                    logger.error(f"Error closing dead session: {str(close_err)}")
                
                # Mark session as inactive in database regardless of blockchain close result
                await session_crud.update_session_status(db, session.id, False)
                
                return {
                    "error": {
                        "message": "Session is dead and has been closed",
                        "type": "DeadSession",
                        "original_error": str(e)
                    }
                }
                
    except Exception as e:
        logger.error(f"Error in ping_session: {str(e)}")
        return {
            "error": {
                "message": f"Error checking session status: {str(e)}",
                "type": "PingError"
            }
        } 