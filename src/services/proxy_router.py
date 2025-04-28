import httpx
import logging
import asyncio
import json
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..crud import private_key as private_key_crud

# Setup logging
logger = logging.getLogger(__name__)

async def execute_proxy_router_operation(
    method: str,
    endpoint: str,
    headers: Dict[str, str] = None,
    json: Dict[str, Any] = None,
    max_retries: int = 3,
    user_id: int = None,
    db: AsyncSession = None,
    params: dict = None
) -> Dict[str, Any]:
    """
    Execute a request to the proxy router with retry logic.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        endpoint: Endpoint path (without base URL)
        headers: Request headers
        json: Request body
        max_retries: Maximum number of retry attempts
        user_id: ID of the user whose private key to use (optional)
        db: Database session (optional)
        params: Query parameters (optional)
        
    Returns:
        Dict: Response data as dictionary
        
    Raises:
        ValueError: If the request fails after all retries
    """
    # Get user's private key if user_id and db are provided
    private_key = None
    using_fallback = False
    
    if user_id and db:
        # Use the get_private_key_with_fallback function
        private_key, using_fallback = await private_key_crud.get_private_key_with_fallback(db, user_id)
        
        if not private_key:
            raise ValueError(
                "Private key not found and no fallback key configured. Please set up your private key."
            )
    
    # Set up headers with private key if available
    request_headers = headers or {}
    if private_key:
        request_headers["X-Private-Key"] = private_key
        
        # Log a warning if using fallback key (for debugging purposes only)
        if using_fallback:
            logger.warning("DEBUGGING MODE: Using fallback private key - this should never be used in production!")
    
    # Set up auth credentials
    auth = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)
    
    # Build the full URL
    base_url = settings.PROXY_ROUTER_URL
    url = f"{base_url}/{endpoint.lstrip('/')}"
    
    async with httpx.AsyncClient() as client:
        for attempt in range(max_retries):
            try:
                logger.info(f"Making {method} request to {url} (attempt {attempt+1}/{max_retries})")
                response = await client.request(
                    method,
                    url,
                    headers=request_headers,
                    json=json,
                    params=params,
                    auth=auth,
                    timeout=30.0
                )
                response.raise_for_status()
                
                # For DELETE operations or other cases where no JSON response is expected
                if method.upper() == "DELETE" or response.status_code == 204:
                    return {}
                
                return response.json()
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                if attempt == max_retries - 1:
                    # If this was the last attempt, raise the error
                    logger.error(f"Failed after {max_retries} attempts: {str(e)}")
                    
                    # Include information about fallback key in error message if applicable  
                    error_message = f"Error from proxy router after {max_retries} attempts: {str(e)}"
                    if using_fallback:
                        error_message = f"[USING FALLBACK KEY] {error_message}"
                    
                    raise ValueError(error_message)
                
                # Wait with exponential backoff before retrying
                backoff_time = 1 * (attempt + 1)  # 1, 2, 3... seconds
                logger.warning(f"Request failed, retrying in {backoff_time} seconds... ({attempt+1}/{max_retries})")
                await asyncio.sleep(backoff_time)

def handle_proxy_error(e, operation_name):
    """
    Common error handling for proxy router errors
    
    Args:
        e: The exception that occurred
        operation_name: Description of the operation being performed
        
    Returns:
        Dict: Error response to return to the client
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
        logger.error(f"Error {operation_name}: {e}")
        return {
            "error": {
                "message": f"Unexpected error {operation_name}: {str(e)}",
                "type": str(type(e).__name__),
                "details": str(e)
            }
        } 