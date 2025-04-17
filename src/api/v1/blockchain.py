from fastapi import APIRouter, HTTPException, status, Query, Path, Body
from typing import Dict, Any, Optional
import httpx
import json

from ...core.config import settings

router = APIRouter(tags=["blockchain"])

# Authentication credentials
AUTH = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)

def handle_proxy_error(e, operation_name):
    """Common error handling for proxy router errors"""
    import logging
    
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
):
    """
    Approve the contract to spend MOR tokens on your behalf.
    
    Connects to the proxy-router's /blockchain/approve endpoint.
    For creating sessions, approve enough tokens by calculating: bid_price * session_duration
    """
    try:
        endpoint = f"{settings.PROXY_ROUTER_URL}/blockchain/approve"
        params = {"spender": spender, "amount": amount}
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                endpoint,
                params=params,
                auth=AUTH,
                timeout=30.0
            )
            response.raise_for_status()
            
            return response.json()
    except Exception as e:
        handle_proxy_error(e, "approving spending")

@router.post("/sessions/bid")
async def create_bid_session(
    bid_id: str = Query(..., description="The blockchain ID (hex) of the bid to create a session for"),
    session_data: Dict[str, Any] = Body(..., example={"sessionDuration": 300}, description="Session data including sessionDuration in seconds"),
):
    """
    Create a session with a provider using a bid ID.
    
    Connects to the proxy-router's /blockchain/bids/{id}/session endpoint.
    Note: Use the blockchain bid ID (hex), not the name.
    For testing, a 5-minute session would have sessionDuration: 300
    """
    try:
        endpoint = f"{settings.PROXY_ROUTER_URL}/blockchain/bids/{bid_id}/session"
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                endpoint,
                json=session_data,
                auth=AUTH,
                timeout=30.0
            )
            response.raise_for_status()
            
            return response.json()
    except Exception as e:
        handle_proxy_error(e, "creating bid session")

@router.post("/sessions/model")
async def create_model_session(
    model_id: str = Query(..., description="The blockchain ID (hex) of the model to create a session for"),
    session_data: Dict[str, Any] = Body(..., example={"sessionDuration": 300}, description="Session data including sessionDuration in seconds"),
):
    """
    Create a session with a provider using a model ID.
    
    Connects to the proxy-router's /blockchain/models/{id}/session endpoint.
    Note: Use the blockchain model ID (hex), not the name.
    For testing, a 5-minute session would have sessionDuration: 300
    """
    try:
        endpoint = f"{settings.PROXY_ROUTER_URL}/blockchain/models/{model_id}/session"
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                endpoint,
                json=session_data,
                auth=AUTH,
                timeout=30.0
            )
            response.raise_for_status()
            
            return response.json()
    except Exception as e:
        handle_proxy_error(e, "creating model session")

@router.get("/sessions/open")
async def get_open_sessions():
    """
    Get all open sessions.
    
    Connects to the proxy-router's /blockchain/sessions endpoint.
    Returns all active sessions for the current wallet.
    """
    try:
        endpoint = f"{settings.PROXY_ROUTER_URL}/blockchain/sessions"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                endpoint,
                auth=AUTH,
                timeout=10.0
            )
            response.raise_for_status()
            
            return response.json()
    except Exception as e:
        handle_proxy_error(e, "getting open sessions")

@router.post("/sessions/close")
async def close_session(
    session_id: str = Query(..., description="The blockchain ID (hex) of the session to close"),
):
    """
    Close a session.
    
    Connects to the proxy-router's /blockchain/sessions/{id}/close endpoint.
    Note: Use the blockchain session ID (hex) returned when creating a session.
    """
    try:
        endpoint = f"{settings.PROXY_ROUTER_URL}/blockchain/sessions/{session_id}/close"
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                endpoint,
                auth=AUTH,
                timeout=30.0
            )
            response.raise_for_status()
            
            return response.json()
    except Exception as e:
        handle_proxy_error(e, "closing session")

@router.post("/sessions/ping")
async def ping_provider():
    """
    Ping the provider to check if it's online.
    
    Connects to the proxy-router's /proxy/provider/ping endpoint.
    Use this to verify connectivity with the provider.
    """
    try:
        endpoint = f"{settings.PROXY_ROUTER_URL}/proxy/provider/ping"
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                endpoint,
                auth=AUTH,
                timeout=10.0
            )
            response.raise_for_status()
            
            return response.json()
    except Exception as e:
        handle_proxy_error(e, "pinging provider") 