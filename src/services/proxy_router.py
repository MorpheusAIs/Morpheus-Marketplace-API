import httpx
import logging
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..crud import private_key as private_key_crud

# Setup logging
logger = logging.getLogger(__name__)

async def execute_proxy_router_operation(
    endpoint: str, 
    data: dict = None, 
    params: dict = None, 
    user_id: int = None, 
    db: AsyncSession = None,
    method: str = "POST",
    headers: dict = None
):
    """
    Execute an operation on the proxy router with the user's private key in the header.
    
    Args:
        endpoint: The proxy router endpoint to call (without base URL)
        data: JSON data to send in the request body
        params: Query parameters to include in the request
        user_id: ID of the user whose private key to use
        db: Database session
        method: HTTP method to use (default: POST)
        headers: Additional headers to include in the request
        
    Returns:
        Response data from the proxy router
        
    Raises:
        HTTPException: If the operation fails
    """
    # Get user's private key if user_id and db are provided
    private_key = None
    if user_id and db:
        private_key = await private_key_crud.get_decrypted_private_key(db, user_id)
        if not private_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Private key not found. Please set up your private key first."
            )
    
    # Set up headers with private key if available
    request_headers = headers or {}
    if private_key:
        request_headers["X-Private-Key"] = private_key
    
    # Set up auth credentials
    auth = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)
    
    # Build the full URL
    full_url = f"{settings.PROXY_ROUTER_URL}/{endpoint.lstrip('/')}"
    
    try:
        async with httpx.AsyncClient() as client:
            if method.upper() == "POST":
                response = await client.post(
                    full_url,
                    json=data,
                    params=params,
                    headers=request_headers,
                    auth=auth,
                    timeout=30.0
                )
            elif method.upper() == "GET":
                response = await client.get(
                    full_url,
                    params=params,
                    headers=request_headers,
                    auth=auth,
                    timeout=30.0
                )
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
                
            response.raise_for_status()
            return response.json()
            
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error calling proxy router: {e}")
        
        # Try to extract detailed error information
        try:
            error_detail = e.response.json()
            if isinstance(error_detail, dict):
                if "error" in error_detail:
                    detail_message = error_detail["error"]
                elif "detail" in error_detail:
                    detail_message = error_detail["detail"]
                else:
                    detail_message = str(error_detail)
            else:
                detail_message = str(error_detail)
        except:
            detail_message = f"Status code: {e.response.status_code}, Reason: {e.response.reason_phrase}"
            
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Error from proxy router: {detail_message}"
        )
    except Exception as e:
        logger.error(f"Unexpected error calling proxy router: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error communicating with proxy router: {str(e)}"
        )

def handle_proxy_error(e, operation_name):
    """
    Common error handling for proxy router errors
    
    Args:
        e: The exception that occurred
        operation_name: Description of the operation being performed
        
    Raises:
        HTTPException: With appropriate status code and detail
    """
    if isinstance(e, httpx.HTTPStatusError):
        logger.error(f"HTTP error during {operation_name}: {e}")
        
        # Try to extract detailed error information
        try:
            error_detail = e.response.json()
            if isinstance(error_detail, dict):
                if "error" in error_detail:
                    detail_message = error_detail["error"]
                elif "detail" in error_detail:
                    detail_message = error_detail["detail"]
                else:
                    detail_message = str(error_detail)
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
        logger.error(f"Error {operation_name}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error {operation_name}: {str(e)}"
        ) 