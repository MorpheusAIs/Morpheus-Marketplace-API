from fastapi import APIRouter, HTTPException, status, Query, Body, Depends
from typing import Dict, Any, Optional
import httpx
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...db.database import get_db
from ...dependencies import get_api_key_user
from ...db.models import User
from ...crud import session as session_crud
from ...crud import api_key as api_key_crud
from ...crud import private_key as private_key_crud
from ...services.proxy_router import execute_proxy_router_operation, handle_proxy_error

router = APIRouter(tags=["Session"])

# Authentication credentials
AUTH = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)

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
            
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Error {operation_name}: {detail_message}"
        )
    else:
        # Handle other errors
        logging.error(f"Error {operation_name}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error {operation_name}: {str(e)}"
        )

@router.post("/approve")
async def approve_spending(
    spender: str = Query(..., description="The address of the spender contract (hex)"),
    amount: int = Query(..., description="The amount to approve, consider bid price * duration for sessions"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_api_key_user)
):
    """
    Approve the contract to spend MOR tokens on your behalf.
    
    Connects to the proxy-router's /blockchain/approve endpoint.
    For creating sessions, approve enough tokens by calculating: bid_price * session_duration
    """
    try:
        # Get user's private key
        private_key = await private_key_crud.get_decrypted_private_key(db, user.id)
        if not private_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No private key found for user. Please set up your private key first."
            )
        
        # Call the proxy router with the private key in the header
        response = await execute_proxy_router_operation(
            endpoint="blockchain/approve",
            params={"spender": spender, "amount": amount},
            user_id=user.id,
            db=db
        )
        
        return response
    except Exception as e:
        handle_proxy_error(e, "approving spending")

@router.post("/bidsession")
async def create_bid_session(
    bid_id: str = Query(..., description="The blockchain ID (hex) of the bid to create a session for"),
    session_data: Dict[str, Any] = Body(..., example={"sessionDuration": 3600}, description="Session data including sessionDuration in seconds"),
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
    
    # Get the API key used for authentication
    api_key_prefix = user.api_keys[0].key_prefix if user.api_keys else None
    if not api_key_prefix:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No API key found for this user"
        )
    
    api_key = await api_key_crud.get_api_key_by_prefix(db, api_key_prefix)
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
            # Use the utility function to close the session with private key
            await execute_proxy_router_operation(
                endpoint=f"blockchain/sessions/{existing_session.session_id}/close",
                user_id=user.id,
                db=db
            )
        except Exception as e:
            # Log the error but continue with creating a new session
            logging.warning(f"Failed to close existing session: {str(e)}")
        
        # Mark the session as inactive in the database
        await session_crud.update_session_status(db, existing_session.id, False)
    
    # Create the session with the bid
    try:
        # Use the utility function to create a session with private key
        session_response = await execute_proxy_router_operation(
            endpoint=f"blockchain/bids/{bid_id}/session",
            data=session_data,
            user_id=user.id,
            db=db
        )
        
        blockchain_session_id = session_response.get("session", {}).get("id")
        
        if not blockchain_session_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid session response from proxy-router"
            )
        
        # Store session information in the database
        session_duration = session_data.get("sessionDuration", 3600)
        db_session = await session_crud.create_session(
            db, 
            api_key.id, 
            blockchain_session_id, 
            bid_id,
            session_duration
        )
        
        return {
            "success": True,
            "message": "Session created and associated with API key",
            "session_id": blockchain_session_id,
            "api_key_prefix": api_key_prefix
        }
    except Exception as e:
        handle_proxy_error(e, "creating bid session")

@router.post("/modelsession")
async def create_model_session(
    model_id: str = Query(..., description="The blockchain ID (hex) of the model to create a session for"),
    session_data: Dict[str, Any] = Body(..., example={"sessionDuration": 3600}, description="Session data including sessionDuration in seconds"),
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
    
    # Get the API key used for authentication
    api_key_prefix = user.api_keys[0].key_prefix if user.api_keys else None
    if not api_key_prefix:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No API key found for this user"
        )
    
    api_key = await api_key_crud.get_api_key_by_prefix(db, api_key_prefix)
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
            # Use the utility function to close the session with private key
            await execute_proxy_router_operation(
                endpoint=f"blockchain/sessions/{existing_session.session_id}/close",
                user_id=user.id,
                db=db
            )
        except Exception as e:
            # Log the error but continue with creating a new session
            logging.warning(f"Failed to close existing session: {str(e)}")
        
        # Mark the session as inactive in the database
        await session_crud.update_session_status(db, existing_session.id, False)
    
    # Create the session with the model
    try:
        # Use the utility function to create a session with private key
        session_response = await execute_proxy_router_operation(
            endpoint=f"blockchain/models/{model_id}/session",
            data=session_data,
            user_id=user.id,
            db=db
        )
        
        blockchain_session_id = session_response.get("session", {}).get("id")
        
        if not blockchain_session_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid session response from proxy-router"
            )
        
        # Store session information in the database
        session_duration = session_data.get("sessionDuration", 3600)
        db_session = await session_crud.create_session(
            db, 
            api_key.id, 
            blockchain_session_id, 
            model_id,
            session_duration
        )
        
        return {
            "success": True,
            "message": "Session created and associated with API key",
            "session_id": blockchain_session_id,
            "api_key_prefix": api_key_prefix
        }
    except Exception as e:
        handle_proxy_error(e, "creating model session")

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
    
    # Get the API key used for authentication
    api_key_prefix = user.api_keys[0].key_prefix if user.api_keys else None
    if not api_key_prefix:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No API key found for this user"
        )
    
    db_api_key = await api_key_crud.get_api_key_by_prefix(db, api_key_prefix)
    if not db_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API key not found"
        )
    
    # Get session associated with the API key
    session = await session_crud.get_session_by_api_key_id(db, db_api_key.id)
    
    if not session or not session.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active session found for this API key"
        )
    
    try:
        # Use our utility function to close the session with private key
        close_response = await execute_proxy_router_operation(
            endpoint=f"blockchain/sessions/{session.session_id}/close",
            user_id=user.id,
            db=db
        )
        
        # Mark the session as inactive in the database
        await session_crud.update_session_status(db, session.id, False)
        
        return {
            "success": True,
            "message": "Session closed successfully",
            "session_id": session.session_id
        }
    except Exception as e:
        handle_proxy_error(e, "closing session")

@router.post("/pingsession")
async def ping_session(
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Ping an active session to keep it alive.
    
    Sessions that are not used or pinged will eventually expire.
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get the API key used for authentication
    api_key_prefix = user.api_keys[0].key_prefix if user.api_keys else None
    if not api_key_prefix:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No API key found for this user"
        )
    
    db_api_key = await api_key_crud.get_api_key_by_prefix(db, api_key_prefix)
    if not db_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API key not found"
        )
    
    # Get session associated with the API key
    session = await session_crud.get_session_by_api_key_id(db, db_api_key.id)
    
    if not session or not session.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active session found for this API key"
        )
    
    try:
        # Use our utility function to ping the session with private key
        ping_response = await execute_proxy_router_operation(
            endpoint=f"blockchain/sessions/{session.session_id}/ping",
            user_id=user.id,
            db=db
        )
        
        return {
            "success": True,
            "message": "Session pinged successfully",
            "session_id": session.session_id
        }
    except Exception as e:
        handle_proxy_error(e, "pinging session") 