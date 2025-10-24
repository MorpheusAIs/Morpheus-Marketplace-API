from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Optional, Any

import httpx
from ..core.config import settings
from ..crud import private_key as private_key_crud
from ..core.logging_config import get_proxy_logger
import base64

logger = get_proxy_logger()

# Singleton HTTP client for connection pooling
_http_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


async def get_http_client() -> httpx.AsyncClient:
    """
    Get or create singleton HTTP client with connection pooling.
    
    This prevents creating a new HTTP client for every request, which:
    - Reduces connection overhead
    - Enables connection keep-alive and pooling
    - Prevents socket exhaustion on rapid requests
    - Improves performance through connection reuse
    """
    global _http_client
    
    if _http_client is None:
        async with _client_lock:
            if _http_client is None:
                _http_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        timeout=180.0,  # Overall timeout increased for large token responses
                        connect=10.0,   # Connection timeout
                        read=180.0,     # Read timeout for streaming large responses
                        write=30.0      # Write timeout
                    ),
                    limits=httpx.Limits(
                        max_connections=100,        # Total connection pool
                        max_keepalive_connections=20,  # Keep 20 connections alive
                        keepalive_expiry=30.0       # Keep connections alive for 30s
                    ),
                    http2=True,  # Enable HTTP/2 for better performance (requires httpx[http2])
                    follow_redirects=True,
                )
                logger.info("Initialized singleton HTTP client for proxy router",
                           max_connections=100,
                           max_keepalive=20,
                           timeout=180.0,
                           event_type="http_client_initialized")
    
    return _http_client


async def close_http_client():
    """
    Close singleton HTTP client.
    Should be called on application shutdown.
    """
    global _http_client
    if _http_client is not None:
        logger.info("Closing singleton HTTP client", event_type="http_client_closing")
        await _http_client.aclose()
        _http_client = None
        logger.info("Singleton HTTP client closed", event_type="http_client_closed")


class ProxyRouterServiceError(Exception):
    """Custom exception for proxy router service errors."""
    
    def __init__(self, message: str, status_code: Optional[int] = None, error_type: str = "unknown"):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.message = message
    
    def get_http_status_code(self) -> int:
        """Get the appropriate HTTP status code for this error."""
        # If we have a specific status code from the proxy router, use it
        if self.status_code:
            return self.status_code
        
        # Otherwise, map based on error type
        error_type_mapping = {
            "authentication_error": 401,
            "authorization_error": 403,
            "validation_error": 400,
            "not_found_error": 404,
            "client_error": 400,
            "server_error": 503,
            "network_error": 503,
            "timeout_error": 504,
            "unknown": 500
        }
        
        return error_type_mapping.get(self.error_type, 500)




async def _execute_request(
    method: str,
    endpoint: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json_data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 120.0,  # INCREASED from 30s for large token responses
    max_retries: int = 3,
    user_id: Optional[int] = None,
    db = None,
) -> httpx.Response:
    """
    Execute a request to the proxy router with retry logic and authentication.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        endpoint: Endpoint path (without base URL)
        headers: Additional request headers
        json_data: Request body as JSON
        params: Query parameters
        timeout: Request timeout
        max_retries: Maximum number of retry attempts
        user_id: User ID for private key authentication
        db: Database session for private key lookup
        
    Returns:
        httpx.Response: The response object
        
    Raises:
        ProxyRouterServiceError: If the request fails after all retries
    """
    logger.info("Executing proxy router request",
                method=method,
                endpoint=endpoint,
                event_type="proxy_request_start")
    
    # Build headers with authentication
    request_headers = headers or {}
    
    # Add private key if user_id and db are provided
    if user_id and db:
        logger.debug("Retrieving private key for user",
                    user_id=user_id,
                    event_type="private_key_lookup")
        private_key, using_fallback = await private_key_crud.get_private_key_with_fallback(db, user_id)
        
        if not private_key:
            logger.error("No private key found and no fallback configured",
                        user_id=user_id,
                        event_type="private_key_error")
            raise ProxyRouterServiceError(
                "Private key not found and no fallback key configured",
                status_code=401,
                error_type="authentication_error"
            )
        
        request_headers["X-Private-Key"] = private_key
        logger.debug("Added private key to request",
                    using_fallback=using_fallback,
                    event_type="private_key_added")
    
    # Set up basic auth
    auth = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)
    
    # Build full URL
    base_url = settings.PROXY_ROUTER_URL.rstrip('/')
    url = f"{base_url}/{endpoint.lstrip('/')}"
    logger.debug("Proxy router request URL", url=url)
    
    if json_data:
        logger.debug("Proxy router request body", request_body=json_data)
    
    # Use singleton HTTP client for connection pooling and performance
    client = await get_http_client()
    
    for attempt in range(max_retries):
        try:
            logger.debug("Making proxy router request attempt",
                        method=method,
                        attempt=attempt+1,
                        max_retries=max_retries)
            response = await client.request(
                method,
                url,
                headers=request_headers,
                json=json_data,
                params=params,
                auth=auth,
                timeout=timeout
            )
            
            logger.debug("Proxy router response received",
                        status_code=response.status_code,
                        content_length=len(response.content) if response.content else 0,
                        event_type="proxy_response")
            
            # For successful responses, validate body before returning
            if response.status_code < 400:
                # Check for empty response body
                if not response.content or len(response.content) == 0:
                    logger.error("Proxy router returned empty response body",
                                status_code=response.status_code,
                                url=url,
                                method=method,
                                event_type="proxy_empty_response")
                    raise ProxyRouterServiceError(
                        "Proxy router returned empty response body with 200 OK",
                        status_code=response.status_code,
                        error_type="empty_response"
                    )
                return response
            
            # For client/server errors, raise for status
            response.raise_for_status()
            
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if not status_code:
                status_code = response.status_code
            logger.warning("HTTP error on proxy router request",
                          attempt=attempt+1,
                          status_code=status_code,
                          url=e.response.url,
                          method=method,
                          error = response.text,
                          event_type="proxy_http_error")
            
            if attempt == max_retries - 1:
                # If this was the last attempt, raise with status code info
                logger.error("Proxy router request failed after all retries",
                            max_retries=max_retries,
                            url=e.response.url,
                            method=method,
                            error=response.text,
                            status_code=status_code,
                            event_type="proxy_request_failed")
                error_type = "http_error"
                if status_code >= 500:
                    error_type = "server_error"
                elif status_code >= 400:
                    error_type = "client_error"
                
                raise ProxyRouterServiceError(
                    f"HTTP {status_code}: {response.text}",
                    status_code=status_code,
                    error_type=error_type
                )
            
            # Wait with exponential backoff before retrying
            backoff_time = 1 * (attempt + 1)  # 1, 2, 3... seconds
            logger.info("HTTP error, retrying with backoff",
                       backoff_time=backoff_time,
                       attempt=attempt+1,
                       event_type="proxy_retry_backoff")
            await asyncio.sleep(backoff_time)
            
        except httpx.RequestError as e:
            logger.warning("Request error on proxy router attempt",
                          attempt=attempt+1,
                          url=url,
                          method=method,
                          error=str(e),
                          event_type="proxy_request_error")
            
            if attempt == max_retries - 1:
                # If this was the last attempt, raise the error
                logger.error("Proxy router request failed after all attempts",
                            max_retries=max_retries,
                            error=str(e),
                            event_type="proxy_request_failed")
                raise ProxyRouterServiceError(
                    f"Request failed after {max_retries} attempts: {str(e)}",
                    error_type="network_error"
                )
            
            # Wait with exponential backoff before retrying
            backoff_time = 1 * (attempt + 1)  # 1, 2, 3... seconds
            logger.info("Request error, retrying with backoff",
                       backoff_time=backoff_time,
                       attempt=attempt+1,
                       event_type="proxy_retry_backoff")
            await asyncio.sleep(backoff_time)
    
    # Should never reach here, but just in case
    raise ProxyRouterServiceError(f"Request failed after {max_retries} attempts")


async def openSession(
    *,
    target_model: str,
    session_duration: int = 3600,
    user_id: int,
    db,
    failover: bool = False,
    direct_payment: bool = False,
) -> Dict[str, Any]:
    """
    Open a new session with the proxy router.
    
    Args:
        target_model: Blockchain ID of the model (hex string starting with 0x)
        session_duration: Session duration in seconds
        user_id: User ID for private key authentication
        db: Database session for private key lookup
        failover: Whether to enable failover
        direct_payment: Whether to use direct payment
        
    Returns:
        Dict containing session information including sessionID
        
    Raises:
        ProxyRouterServiceError: If session creation fails
    """
    logger.info("Opening proxy router session",
               target_model=target_model,
               event_type="session_open_start")
    
    # Validate model format
    if not target_model.startswith("0x"):
        raise ProxyRouterServiceError(
            f"Invalid blockchain ID format: {target_model}. Expected hex string starting with '0x'",
            status_code=400,
            error_type="validation_error"
        )
    
    session_data = {
        "sessionDuration": session_duration,
        "failover": failover,
        "directPayment": direct_payment
    }
    
    headers = {"Content-Type": "application/json"}
    
    try:
        response = await _execute_request(
            "POST",
            f"blockchain/models/{target_model}/session",
            headers=headers,
            json_data=session_data,
            user_id=user_id,
            db=db,
            max_retries=3
        )
        
        result = response.json()
        logger.info("Session created successfully",
                   target_model=target_model,
                   session_id=result.get("sessionID"),
                   event_type="session_created")
        return result
        
    except Exception as e:
        logger.error("Error creating session",
                    target_model=target_model,
                    error=str(e),
                    event_type="session_creation_error")
        raise ProxyRouterServiceError(f"Failed to create session: {str(e)}")


async def closeSession(session_id: str) -> Dict[str, Any]:
    """
    Close an existing session.
    
    Args:
        session_id: ID of the session to close
        
    Returns:
        Dict containing closure response
        
    Raises:
        ProxyRouterServiceError: If session closure fails
    """
    logger.info("Closing proxy router session",
               session_id=session_id,
               event_type="session_close_start")
    
    try:
        response = await _execute_request(
            "POST",
            f"blockchain/sessions/{session_id}/close",
            max_retries=3
        )
        
        # Handle successful closure
        if response.status_code == 200:
            try:
                result = response.json()
                logger.info("Session closed successfully with JSON response",
                           session_id=session_id,
                           response_data=result,
                           event_type="session_closed_json")
                return result
            except json.JSONDecodeError:
                # Some endpoints may not return JSON
                logger.info("Session closed successfully (no JSON response)",
                           session_id=session_id,
                           event_type="session_closed_no_json")
                return {"success": True}
        elif response.status_code == 204:
            logger.info("Session closed successfully (204 No Content)",
                       session_id=session_id,
                       event_type="session_closed_no_content")
            return {"success": True}
        else:
            raise ProxyRouterServiceError(
                f"Unexpected response status: {response.status_code}",
                status_code=response.status_code,
                error_type="server_error" if response.status_code >= 500 else "client_error"
            )
        
    except Exception as e:
        logger.error("Error closing session",
                    session_id=session_id,
                    error=str(e),
                    event_type="session_close_error")
        raise ProxyRouterServiceError(f"Failed to close session: {str(e)}")


async def getSessionStatus(session_id: str) -> Dict[str, Any]:
    """
    Get the status of a session.
    
    Args:
        session_id: ID of the session to check
        
    Returns:
        Dict containing session status information
        
    Raises:
        ProxyRouterServiceError: If status check fails
    """
    logger.info("Getting session status",
               session_id=session_id,
               event_type="session_status_check_start")
    
    try:
        response = await _execute_request(
            "GET",
            f"blockchain/sessions/{session_id}",
            max_retries=2
        )
        
        result = response.json()
        logger.info("Session status retrieved successfully",
                   session_id=session_id,
                   status_data=result,
                   event_type="session_status_retrieved")
        return result
        
    except Exception as e:
        logger.error("Error getting session status",
                    session_id=session_id,
                    error=str(e),
                    event_type="session_status_error")
        raise ProxyRouterServiceError(f"Failed to get session status: {str(e)}")


async def getBidDetails(bid_id: str) -> Dict[str, Any]:
    """
    Get details of a specific bid.
    
    Args:
        bid_id: ID of the bid to get details for
        
    Returns:
        Dict containing bid details
        
    Raises:
        ProxyRouterServiceError: If getting bid details fails
    """
    logger.info("Getting bid details",
               bid_id=bid_id,
               event_type="bid_details_fetch_start")
    
    try:
        response = await _execute_request(
            "GET",
            f"blockchain/bids/{bid_id}",
            max_retries=2
        )
        
        result = response.json()
        logger.info("Bid details retrieved successfully",
                   bid_id=bid_id,
                   bid_data=result,
                   event_type="bid_details_retrieved")
        return result
        
    except Exception as e:
        logger.error("Error getting bid details",
                    bid_id=bid_id,
                    error=str(e),
                    event_type="bid_details_error")
        raise ProxyRouterServiceError(f"Failed to get bid details: {str(e)}")


async def createBidSession(
    *,
    bid_id: str,
    session_data: Dict[str, Any],
    user_id: int,
    db,
    chain_id: Optional[str] = None,
    contract_address: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a session for a specific bid.
    
    Args:
        bid_id: ID of the bid to create session for
        session_data: Session configuration data
        user_id: User ID for private key authentication
        db: Database session for private key lookup
        chain_id: Blockchain chain ID (optional)
        contract_address: Contract address (optional)
        
    Returns:
        Dict containing session information including sessionID
        
    Raises:
        ProxyRouterServiceError: If session creation fails
    """
    logger.info("Creating bid session",
               bid_id=bid_id,
               user_id=user_id,
               event_type="bid_session_creation_start")
    
    # Build headers with additional blockchain info
    headers = {"Content-Type": "application/json"}
    if chain_id:
        headers["X-Chain-ID"] = chain_id
    if contract_address:
        headers["X-Contract-Address"] = contract_address
    
    try:
        response = await _execute_request(
            "POST",
            f"blockchain/bids/{bid_id}/session",
            headers=headers,
            json_data=session_data,
            user_id=user_id,
            db=db,
            max_retries=3
        )
        
        result = response.json()
        logger.info("Bid session created successfully",
                   bid_id=bid_id,
                   session_id=result.get("sessionID"),
                   event_type="bid_session_created")
        return result
        
    except Exception as e:
        logger.error("Error creating bid session",
                    bid_id=bid_id,
                    error=str(e),
                    event_type="bid_session_creation_error")
        raise ProxyRouterServiceError(f"Failed to create bid session: {str(e)}")


async def getModels(headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    """Call the proxy router /v1/models endpoint and return the response.

    Uses the new retry logic and authentication system.
    """
    logger.info("Getting available models from proxy router",
               event_type="proxy_models_fetch_start")
    
    try:
        response = await _execute_request(
            "GET",
            "v1/models",
            headers=headers,
            timeout=5.0,
            max_retries=2
        )
        return response
    except Exception as e:
        logger.error("Error getting models from proxy router",
                    error=str(e),
                    event_type="proxy_models_fetch_error")
        raise ProxyRouterServiceError(f"Failed to get models: {str(e)}")


def _basic_auth_header_from_settings() -> Dict[str, str]:
    auth_str = f"{settings.PROXY_ROUTER_USERNAME}:{settings.PROXY_ROUTER_PASSWORD}"
    auth_b64 = base64.b64encode(auth_str.encode("ascii")).decode("ascii")
    return {"authorization": f"Basic {auth_b64}"}


async def chatCompletions(
    *,
    session_id: str,
    messages: list,
    **kwargs
) -> httpx.Response:
    """
    Send a non-streaming chat completions request to the proxy router.
    
    Args:
        session_id: Session ID for the chat request
        messages: List of chat messages
        **kwargs: Additional parameters for the chat completion
        
    Returns:
        httpx.Response: The response object (non-streaming)
        
    Raises:
        ProxyRouterServiceError: If the request fails
    """
    logger.info("Chat completions request",
               session_id=session_id,
               message_count=len(messages),
               event_type="chat_completions_start")
    
    # Build the request payload
    payload = {
        "messages": messages,
        "stream": False,
        **kwargs
    }
    
    # Build headers
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "session_id": session_id,
        "X-Session-ID": session_id,
    }
    
    # Add basic auth
    auth_str = f"{settings.PROXY_ROUTER_USERNAME}:{settings.PROXY_ROUTER_PASSWORD}"
    auth_b64 = base64.b64encode(auth_str.encode("ascii")).decode("ascii")
    headers["authorization"] = f"Basic {auth_b64}"
    
    try:
        # For non-streaming requests, use the standard retry logic
        # Increased timeout for large token responses (user experiencing issues at ~7K tokens)
        response = await _execute_request(
            "POST",
            f"v1/chat/completions?session_id={session_id}",
            headers=headers,
            json_data=payload,
            timeout=180.0,  # 3 minutes for large token responses
            max_retries=2   # Reduced retries since timeout is longer
        )
        return response
            
    except Exception as e:
        logger.error("Chat completions error",
                    session_id=session_id,
                    error=str(e),
                    event_type="chat_completions_error")
        if isinstance(e, ProxyRouterServiceError):
            raise
        raise ProxyRouterServiceError(f"Failed to send chat completions: {str(e)}")


@asynccontextmanager
async def chatCompletionsStream(
    *,
    session_id: str,
    messages: list,
    **kwargs
) -> AsyncIterator[httpx.Response]:
    """
    Create a streaming chat completions request.
    
    Args:
        session_id: Session ID for the chat request
        messages: List of chat messages
        **kwargs: Additional parameters for the chat completion
        
    Yields:
        httpx.Response: The streaming response object
        
    Raises:
        ProxyRouterServiceError: If the request fails
    """
    logger.info("Chat completions stream request",
               session_id=session_id,
               message_count=len(messages),
               event_type="chat_completions_stream_start")
    
    # Build the request payload
    payload = {
        "messages": messages,
        "stream": True,
        **kwargs
    }
    
    # Build headers
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "session_id": session_id,
        "X-Session-ID": session_id,
    }
    
    # Add basic auth
    auth_str = f"{settings.PROXY_ROUTER_USERNAME}:{settings.PROXY_ROUTER_PASSWORD}"
    auth_b64 = base64.b64encode(auth_str.encode("ascii")).decode("ascii")
    headers["authorization"] = f"Basic {auth_b64}"
    
    # Build URL with session_id parameter
    url = f"{settings.PROXY_ROUTER_URL.rstrip('/')}/v1/chat/completions?session_id={session_id}"
    
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                url,
                json=payload,
                headers=headers,
                timeout=60.0
            ) as response:
                # Check for errors
                if response.status_code >= 400:
                    error_type = "server_error" if response.status_code >= 500 else "client_error"
                    raise ProxyRouterServiceError(
                        f"Chat completions stream failed: HTTP {response.status_code}",
                        status_code=response.status_code,
                        error_type=error_type
                    )
                yield response
                
    except Exception as e:
        logger.error("Chat completions stream error",
                    session_id=session_id,
                    error=str(e),
                    event_type="chat_completions_stream_error")
        if isinstance(e, ProxyRouterServiceError):
            raise
        raise ProxyRouterServiceError(f"Failed to create chat completions stream: {str(e)}")


async def embeddings(
    *,
    session_id: str,
    input_data: Any,
    encoding_format: Optional[str] = "float",
    dimensions: Optional[int] = None,
    user: Optional[str] = None
) -> httpx.Response:
    """
    Send an embeddings request to the proxy router.
    
    Args:
        session_id: Session ID for the embeddings request
        input_data: Text input(s) to embed (string or list of strings)
        encoding_format: Encoding format for embeddings
        dimensions: Number of dimensions (optional)
        user: User identifier (optional)
        
    Returns:
        httpx.Response: The response object
        
    Raises:
        ProxyRouterServiceError: If the request fails
    """
    logger.info("Embeddings request",
               session_id=session_id,
               event_type="embeddings_request_start")
    
    # Build the request payload
    payload = {
        "input": input_data,
        "encoding_format": encoding_format
    }
    
    if dimensions:
        payload["dimensions"] = dimensions
    if user:
        payload["user"] = user
    
    # Build headers
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "session_id": session_id,
    }
    
    try:
        response = await _execute_request(
            "POST",
            "v1/embeddings",
            headers=headers,
            json_data=payload,
            timeout=60.0,
            max_retries=3
        )
        return response
            
    except Exception as e:
        logger.error("Embeddings request error",
                    session_id=session_id,
                    error=str(e),
                    event_type="embeddings_request_error")
        if isinstance(e, ProxyRouterServiceError):
            raise
        raise ProxyRouterServiceError(f"Failed to send embeddings request: {str(e)}")


async def getAllModels() -> httpx.Response:
    """
    Get all models from the blockchain.
    
    Returns:
        httpx.Response: The response object containing all models
        
    Raises:
        ProxyRouterServiceError: If the request fails
    """
    logger.info("Getting all models from blockchain",
               event_type="get_all_models_start")
    
    try:
        response = await _execute_request(
            "GET",
            "blockchain/models",
            timeout=10.0,
            max_retries=2
        )
        return response
    except Exception as e:
        logger.error("Error getting all models from blockchain",
                    error=str(e),
                    event_type="get_all_models_error")
        if isinstance(e, ProxyRouterServiceError):
            raise
        raise ProxyRouterServiceError(f"Failed to get all models: {str(e)}")


async def getRatedBids(model_id: str) -> httpx.Response:
    """
    Get rated bids for a specific model.
    
    Args:
        model_id: The blockchain ID (hex) of the model
        
    Returns:
        httpx.Response: The response object containing rated bids
        
    Raises:
        ProxyRouterServiceError: If the request fails
    """
    logger.info("Getting rated bids for model",
               model_id=model_id,
               event_type="get_rated_bids_start")
    
    try:
        response = await _execute_request(
            "GET",
            f"blockchain/models/{model_id}/bids/rated",
            timeout=10.0,
            max_retries=2
        )
        return response
    except Exception as e:
        logger.error("Error getting rated bids for model",
                    model_id=model_id,
                    error=str(e),
                    event_type="get_rated_bids_error")
        if isinstance(e, ProxyRouterServiceError):
            raise
        raise ProxyRouterServiceError(f"Failed to get rated bids for model {model_id}: {str(e)}")


async def approveSpending(
    *,
    spender: str,
    amount: int,
    user_id: int,
    db
) -> httpx.Response:
    """
    Approve a contract to spend MOR tokens on behalf of the user.
    
    Args:
        spender: The contract address to approve as spender
        amount: The amount to approve
        user_id: User ID for private key authentication
        db: Database session for private key lookup
        
    Returns:
        httpx.Response: The response object
        
    Raises:
        ProxyRouterServiceError: If the request fails
    """
    logger.info("Approving spending",
               spender=spender,
               amount=amount,
               user_id=user_id,
               event_type="approve_spending_start")
    
    # Build query parameters
    params = {
        "spender": spender,
        "amount": amount
    }
    
    try:
        response = await _execute_request(
            "POST",
            "blockchain/approve",
            params=params,
            user_id=user_id,
            db=db,
            timeout=30.0,
            max_retries=3
        )
        return response
    except Exception as e:
        logger.error("Error approving spending",
                    spender=spender,
                    amount=amount,
                    user_id=user_id,
                    error=str(e),
                    event_type="approve_spending_error")
        if isinstance(e, ProxyRouterServiceError):
            raise
        raise ProxyRouterServiceError(f"Failed to approve spending: {str(e)}")


async def healthcheck() -> httpx.Response:
    """GET /healthcheck on proxy router with retry logic."""
    logger.info("Performing proxy router health check",
               event_type="proxy_health_check_start")
    
    try:
        response = await _execute_request(
            "GET",
            "healthcheck",
            timeout=5.0,
            max_retries=2
        )
        return response
    except Exception as e:
        logger.error("Proxy router health check failed",
                    error=str(e),
                    event_type="proxy_health_check_failed")
        raise ProxyRouterServiceError(f"Health check failed: {str(e)}")




