# Chat routes 
"""
This module handles chat completion endpoints for the API gateway.

Key behaviors:
- Respects client's 'stream' parameter in requests (true/false)
- Returns streaming responses only when requested (stream=true)
- Returns regular JSON responses when streaming is not requested (stream=false)
- Warning: Tool calling may require streaming with some models
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Body
from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any, List, Union
import json
import httpx
import logging
from datetime import datetime
import uuid
import asyncio
import base64
from pydantic import BaseModel, Field

from ...dependencies import get_api_key_user, api_key_header
from ...db.database import get_db
from ...db.models import User, APIKey
from ...schemas import openai as openai_schemas
from ...crud import session as session_crud
from ...crud import api_key as api_key_crud
from ...core.config import settings
from ...crud import automation as automation_crud
from ...core.model_routing import model_router
from ...services import session_service

router = APIRouter(tags=["Chat"])

# Authentication credentials for proxy-router
AUTH = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)

class ChatMessage(BaseModel):
    role: str
    content: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

class ToolFunction(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Dict[str, Any] = {}

class Tool(BaseModel):
    type: str = "function"
    function: ToolFunction

class ToolChoice(BaseModel):
    type: Optional[str] = "function"
    function: Optional[Dict[str, Any]] = None

class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage]
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    tools: Optional[List[Tool]] = None
    tool_choice: Optional[Union[str, ToolChoice]] = None
    session_id: Optional[str] = Field(None, description="Optional session ID to use for this request. If not provided, the system will use the session associated with the API key.")

async def _handle_automated_session_creation(
    db: AsyncSession,
    user: User,
    db_api_key: APIKey,
    requested_model: Optional[str]
) -> Optional[str]:
    """
    Helper method to handle automated session creation.
    
    Returns:
        session_id if a session was created, None otherwise
    """
    logger = logging.getLogger(__name__)
    
    # Check system-wide feature flag first
    if not settings.AUTOMATION_FEATURE_ENABLED:
        logger.info("Automation feature is disabled system-wide")
        return None
        
    # Check if automation is enabled for the user in their settings
    automation_settings = await automation_crud.get_automation_settings(db, user.id)
    
    # If settings don't exist yet, create them with automation enabled by default
    if not automation_settings:
        logger.info(f"No automation settings found for user {user.id} - creating default settings with automation enabled")
        automation_settings = await automation_crud.create_automation_settings(
            db=db,
            user_id=user.id,
            is_enabled=True,  # Enable automation by default
            session_duration=3600  # Default 1 hour session
        )
    # If settings exist but automation is disabled, log and return None
    elif not automation_settings.is_enabled:
        logger.info(f"Automation is explicitly disabled for user {user.id}")
        return None
    
    # Automation is enabled - create a new session
    logger.info(f"Automation enabled for user {user.id} - creating new session")
    
    # Create new session with requested model
    session_duration = automation_settings.session_duration
    try:
        logger.info(f"Attempting to create automated session for user {user.id} with model {requested_model}, duration {session_duration}")
        new_session = await session_service.create_automated_session(
            db=db,
            api_key_id=db_api_key.id,
            user_id=user.id,
            requested_model=requested_model,
            session_duration=session_duration
        )
        session_id = new_session.id
        logger.info(f"Created new automated session: {session_id}")
        
        # Add a small delay to ensure the session is fully registered
        logger.info("Adding a brief delay to ensure session is fully registered")
        await asyncio.sleep(1.0)  # 1 second delay
        return session_id
    except Exception as e:
        logger.error(f"Error creating automated session: {e}")
        logger.exception(e)  # Log full stack trace
        # Return None to fall back to manual session handling
        return None

@router.post("/completions")
async def create_chat_completion(
    request_data: ChatCompletionRequest,
    request: Request,
    api_key: str = Depends(api_key_header),
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a chat completion with automatic session creation if enabled.
    
    Set Accept header to 'text/event-stream' for streaming responses, especially for tool calls.
    Set Accept header to 'application/json' for non-streaming responses.
    
    Note: Tool calling requires streaming mode and 'text/event-stream' Accept header.
    """
    logger = logging.getLogger(__name__)
    request_id = str(uuid.uuid4())[:8]  # Generate short request ID for tracing
    logger.info(f"[REQ-{request_id}] New chat completion request received")
    
    original_client_accept_header = request.headers.get("accept", "text/event-stream")
    logger.info(f"[REQ-{request_id}] Client's original Accept header: {original_client_accept_header}")
    
    # Check if we have a valid user from the API key
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Set up logging for this request
    logger.info(f"Processing chat completion request for user {user.id}")
    
    json_body = request_data.model_dump(exclude_none=True)
    has_tools = "tools" in json_body and json_body["tools"]

    # Use the client's stream parameter directly
    # If stream is None (not specified), default to False for consistency
    should_stream = request_data.stream if request_data.stream is not None else False
    
    # Check for tool requests with streaming disabled
    if has_tools and not should_stream:
        logger.warning(f"[REQ-{request_id}] Tool calling requested with stream=false - this may cause issues with some models")
        # We'll respect the client's choice, but log a warning
    
    json_body["stream"] = should_stream
    
    # Set accept header based on streaming preference
    if should_stream:
        accept_header = "text/event-stream"
    else:
        accept_header = original_client_accept_header
    
    logger.info(f"[REQ-{request_id}] Original client Accept: '{original_client_accept_header}', client requested stream: {should_stream}, has_tools: {has_tools}")
    logger.info(f"[REQ-{request_id}] Configured PROXY request: stream={json_body['stream']}, proxy_accept_header='{accept_header}'")

    # Extract necessary fields that were not part of the core OpenAI payload manipulated above
    session_id = json_body.pop("session_id", None)
    requested_model = json_body.pop("model", None)
    
    # Check if this is a tool calling request and if the model supports it
    if has_tools:
        # List of models known to support tool calling - update this list as needed
        tool_calling_models = ["llama-3.3-70b", "claude-3.5", "claude-3-opus", "gpt-4o", "gpt-4", "mistral-large", "gemini-pro"]
        
        if requested_model and requested_model.lower() not in [m.lower() for m in tool_calling_models]:
            logger.warning(f"Model {requested_model} may not support tool calling. Consider using one of: {', '.join(tool_calling_models)}")
    
    # Log tool-related parameters if present (for debugging)
    if "tools" in json_body:
        logger.info(f"Request includes tools: {json.dumps(json_body['tools'])}")
    if "tool_choice" in json_body:
        logger.info(f"Request includes tool_choice: {json.dumps(json_body['tool_choice'])}")
    
    body = json.dumps(json_body).encode('utf-8')
    
    # Log the original request details
    logger.info(f"Original request - session_id: {session_id}, model: {requested_model}")
    
    # Store API key reference at a higher scope for later use in error handling
    db_api_key = None
    
    # If no session_id from body, try to get from database
    if not session_id and user.api_keys:
        try:
            logger.info("No session_id in request, attempting to retrieve or create one")
            api_key_prefix = user.api_keys[0].key_prefix
            db_api_key = await api_key_crud.get_api_key_by_prefix(db, api_key_prefix)
            
            if not db_api_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="API key not found"
                )
            
            # Get session associated with the API key
            session = await session_crud.get_session_by_api_key_id(db, db_api_key.id)
            
            if session and session.is_active:
                # Only compare models when a specific model is requested
                if requested_model:
                    # Convert the requested model name to model ID for proper comparison
                    logger.info(f"Converting requested model '{requested_model}' to model ID for comparison")
                    try:
                        requested_model_id = await model_router.get_target_model(requested_model)
                        logger.info(f"Requested model '{requested_model}' resolved to ID: {requested_model_id}")
                        
                        # First check if the session is expired before comparing models
                        if session.is_expired:
                            logger.warning(f"Session {session.id} is expired, creating new session regardless of model match")
                            try:
                                new_session = await session_service.create_automated_session(
                                    db=db,
                                    api_key_id=db_api_key.id,
                                    user_id=user.id,
                                    requested_model=requested_model
                                )
                                session_id = new_session.id
                                logger.info(f"Created new session to replace expired session: {session_id}")
                                
                                # Add a small delay to ensure the session is fully registered
                                logger.info("Adding a brief delay to ensure session is fully registered")
                                await asyncio.sleep(1.0)  # 1 second delay
                            except Exception as e:
                                logger.error(f"Error creating new session to replace expired session: {e}")
                                logger.exception(e)
                                raise HTTPException(
                                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail=f"Failed to create new session to replace expired one: {e}"
                                )
                        # Compare model IDs (hash to hash) only for non-expired sessions
                        elif session.model != requested_model_id:
                            logger.info(f"Model change detected. Current: {session.model}, Requested: {requested_model_id}")
                            logger.info(f"Switching models by closing current session and creating new one")
                            
                            # Switch to the new model
                            try:
                                new_session = await session_service.switch_model(
                                    db=db,
                                    api_key_id=db_api_key.id,
                                    user_id=user.id,
                                    new_model=requested_model
                                )
                                session_id = new_session.id
                                logger.info(f"Successfully switched to new model with session: {session_id}")
                                
                                # Add a small delay to ensure the session is fully registered
                                logger.info("Adding a brief delay to ensure session is fully registered")
                                await asyncio.sleep(1.0)  # 1 second delay
                            except Exception as e:
                                logger.error(f"Error switching models: {e}")
                                logger.exception(e)
                                # Create a new session instead of falling back to the expired one
                                try:
                                    logger.info(f"Creating new session after switch_model failure")
                                    new_session = await session_service.create_automated_session(
                                        db=db,
                                        api_key_id=db_api_key.id,
                                        user_id=user.id,
                                        requested_model=requested_model
                                    )
                                    session_id = new_session.id
                                    logger.info(f"Created new replacement session: {session_id}")
                                    await asyncio.sleep(1.0)  # Small delay to ensure registration
                                except Exception as new_err:
                                    logger.error(f"Failed to create new session after switch failure: {new_err}")
                                    raise HTTPException(
                                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                        detail=f"Failed to create new session after model switch failure: {new_err}"
                                    )
                        else:
                            # Models match, use existing session
                            session_id = session.id
                            logger.info(f"Models match (ID: {requested_model_id}), reusing existing session ID: {session_id}")
                    except Exception as e:
                        logger.error(f"Error resolving model ID for '{requested_model}': {e}")
                        logger.exception(e)
                        # Fall back to using existing session
                        session_id = session.id
                        logger.info(f"Using existing session ID due to model resolution error: {session_id}")
                else:
                    # No requested model specified, use existing session
                    session_id = session.id
                    logger.info(f"No specific model requested, reusing existing session ID: {session_id}")
            else:
                logger.info("No active session found, attempting automated session creation")
                # No active session - try automated session creation
                try:
                    # Add detailed debugging
                    logger.info("=========== SESSION DEBUG START ===========")
                    logger.info(f"Attempting to create automated session with: API key ID: {db_api_key.id}, User ID: {user.id}, Model: {requested_model}")
                    logger.info(f"Settings.PROXY_ROUTER_URL: {settings.PROXY_ROUTER_URL}")
                    
                    # Test connection to proxy router before session creation
                    try:
                        async with httpx.AsyncClient() as test_client:
                            test_url = f"{settings.PROXY_ROUTER_URL}/healthcheck"
                            logger.info(f"Testing connection to proxy router at: {test_url}")
                            test_response = await test_client.get(test_url, timeout=5.0)
                            logger.info(f"Proxy router health check status: {test_response.status_code}")
                            if test_response.status_code == 200:
                                logger.info(f"Proxy router health response: {test_response.text[:100]}")
                            else:
                                logger.error(f"Proxy router appears unhealthy: {test_response.status_code} - {test_response.text[:100]}")
                    except Exception as health_err:
                        logger.error(f"Failed to connect to proxy router health endpoint: {str(health_err)}")
                    
                    # Now attempt session creation
                    automated_session = await session_service.create_automated_session(
                        db=db,
                        api_key_id=db_api_key.id,
                        user_id=user.id,
                        requested_model=requested_model
                    )
                    
                    logger.info(f"create_automated_session returned: {automated_session}")
                    if automated_session:
                        # The Session model uses 'id' attribute, not 'session_id'
                        session_id = automated_session.id
                        logger.info(f"Successfully created automated session with ID: {session_id}")
                        
                        # Add a small delay to ensure the session is fully registered
                        logger.info("Adding a brief delay to ensure session is fully registered")
                        await asyncio.sleep(1.0)  # 1 second delay
                    else:
                        # Session creation returned None - generate detailed log
                        logger.error("Session service returned None from create_automated_session")
                        logger.info("Checking if proxy router is available for the requested model")
                        
                        try:
                            async with httpx.AsyncClient() as model_client:
                                model_url = f"{settings.PROXY_ROUTER_URL}/v1/models"
                                logger.info(f"Checking available models at: {model_url}")
                                model_auth = {
                                    "authorization": f"Basic {base64.b64encode(f'{settings.PROXY_ROUTER_USERNAME}:{settings.PROXY_ROUTER_PASSWORD}'.encode()).decode()}"
                                }
                                model_response = await model_client.get(model_url, headers=model_auth, timeout=5.0)
                                logger.info(f"Models API status: {model_response.status_code}")
                                if model_response.status_code == 200:
                                    models_data = model_response.json()
                                    logger.info(f"Available models: {json.dumps(models_data)}")
                                    # Check if requested model is in the list
                                    model_names = [m.get('id', '') for m in models_data.get('data', [])]
                                    if requested_model in model_names:
                                        logger.info(f"Requested model '{requested_model}' is available in proxy router")
                                    else:
                                        logger.error(f"Requested model '{requested_model}' NOT found in available models: {model_names}")
                                else:
                                    logger.error(f"Failed to get models: {model_response.text[:200]}")
                        except Exception as model_err:
                            logger.error(f"Error checking models API: {str(model_err)}")
                        
                        # Session creation failed
                        logger.error("Automated session creation failed.")
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Automated session creation failed. The model provider may be unavailable."
                        )
                    logger.info("=========== SESSION DEBUG END ===========")
                except Exception as e:
                    # Error in session creation
                    logger.error(f"Automated session creation error: {str(e)}")
                    logger.exception(e)  # Log full stack trace
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"An unexpected error occurred during session creation: {e}"
                    )
        except HTTPException as http_exc:
            # Re-raise HTTP exceptions with logging
            logger.error(f"HTTP exception during session handling: {http_exc.detail}")
            raise
        except Exception as e:
            logger.error(f"Error in session handling: {e}")
            logger.exception(e)  # Log full stack trace
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error handling session: {str(e)}"
            )
    
    # If we still don't have a session_id, return an error
    if not session_id:
        logger.error("No session ID after all attempts")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No session ID provided in request and no active session found for API key"
        )
    
    # Forward request to proxy-router
    endpoint = f"{settings.PROXY_ROUTER_URL}/v1/chat/completions"
    
    # Add session_id to both the URL and as a query parameter for maximum compatibility
    endpoint = f"{endpoint}?session_id={session_id}"
    
    # Create basic auth header - this is critical
    auth_str = f"{settings.PROXY_ROUTER_USERNAME}:{settings.PROXY_ROUTER_PASSWORD}"
    auth_b64 = base64.b64encode(auth_str.encode('ascii')).decode('ascii')
    
    # Set headers with the appropriate accept header
    headers = {
        "authorization": f"Basic {auth_b64}",
        "Content-Type": "application/json",
        "accept": accept_header,
        "session_id": session_id,
        "X-Session-ID": session_id  # Try an alternate header format
    }
    
    # Special fix for tool_choice structure - ensure it's properly formatted
    if "tool_choice" in json_body:
        # If we have nested tool_choice, fix it
        if isinstance(json_body["tool_choice"], dict) and "function" in json_body["tool_choice"]:
            func_obj = json_body["tool_choice"]["function"]
            if isinstance(func_obj, dict) and "tool_choice" in func_obj:
                logger.warning(f"Found nested tool_choice, fixing structure: {json.dumps(json_body['tool_choice'])}")
                try:
                    # Extract correct function name
                    if "name" in func_obj.get("tool_choice", {}).get("function", {}):
                        func_name = func_obj["tool_choice"]["function"]["name"]
                        json_body["tool_choice"] = {
                            "type": "function",
                            "function": {
                                "name": func_name
                            }
                        }
                        logger.info(f"Fixed tool_choice to: {json.dumps(json_body['tool_choice'])}")
                except Exception as e:
                    logger.error(f"Error fixing tool_choice: {str(e)}")

    # Additional fix for tool_choice within tools parameters
    if "tools" in json_body:
        for i, tool in enumerate(json_body["tools"]):
            if isinstance(tool, dict) and "function" in tool:
                func = tool["function"]
                if isinstance(func, dict) and "parameters" in func:
                    params = func["parameters"]
                    if isinstance(params, dict) and "tool_choice" in params:
                        logger.warning(f"Found tool_choice in tool parameters, removing: {json.dumps(params['tool_choice'])}")
                        # Remove tool_choice from parameters
                        del json_body["tools"][i]["function"]["parameters"]["tool_choice"]
                        logger.info(f"Removed tool_choice from tool parameters for tool: {func.get('name')}")

    # Special handling for message with tool_calls and empty content
    if "messages" in json_body:
        for i, msg in enumerate(json_body["messages"]):
            if isinstance(msg, dict) and msg.get("role") == "assistant" and "tool_calls" in msg:
                if msg.get("content") == "":
                    logger.info(f"Setting null content for assistant message with tool_calls at index {i}")
                    json_body["messages"][i]["content"] = None

    # Log complete request for debugging when tools are used
    has_tools = "tools" in json_body
    has_tool_messages = False
    if "messages" in json_body:
        has_tool_messages = any(msg.get("role") == "tool" for msg in json_body["messages"] if isinstance(msg, dict))

    if has_tools or has_tool_messages:
        logger.info("===== TOOL CALLING REQUEST =====")
        logger.info(f"Endpoint: {endpoint}")
        logger.info(f"Headers: {json.dumps({k: v for k, v in headers.items() if k.lower() != 'authorization'})}")
        logger.info(f"Request body: {json.dumps(json_body, indent=2)}")
        logger.info(f"Session ID: {session_id}")
        logger.info("================================")
    
    # Handle streaming only - assume all requests are streaming
    async def stream_generator():
        stream_trace_id = str(uuid.uuid4())[:8]
        logger.info(f"[STREAM-{stream_trace_id}] Starting stream generator for session: {session_id}")
        chunk_count = 0
        req_body_json = None
        
        try:
            # Parse the request body for debugging - do this before the request
            try:
                req_body_json = json.loads(body.decode('utf-8'))
                has_tool_msg = any(msg.get("role") == "tool" for msg in req_body_json.get("messages", []) if isinstance(msg, dict))
                has_tool_calls = any("tool_calls" in msg for msg in req_body_json.get("messages", []) if isinstance(msg, dict))
                
                if has_tool_msg or has_tool_calls:
                    logger.info(f"[STREAM-{stream_trace_id}] Request contains tool messages: {has_tool_msg}, tool calls: {has_tool_calls}")
            except Exception as parse_err:
                logger.error(f"[STREAM-{stream_trace_id}] Failed to parse request body: {parse_err}")
            
            # First attempt with existing session
            logger.info(f"[STREAM-{stream_trace_id}] Making request to proxy router: {endpoint}")
            
            # Track if we need to retry due to expired session
            retry_with_new_session = False
            new_session_id = None
            
            async with httpx.AsyncClient() as client:
                async with client.stream("POST", endpoint, content=body, headers=headers, timeout=60.0) as response:
                    # Log proxy status
                    logger.info(f"[STREAM-{stream_trace_id}] Proxy router responded with status: {response.status_code}")
                    logger.info(f"[STREAM-{stream_trace_id}] Response headers: {dict(response.headers.items())}")
                    
                    if response.status_code != 200:
                        logger.error(f"[STREAM-{stream_trace_id}] Proxy router error response: {response.status_code}")
                        # Try to read and log the error body
                        try:
                            error_body = await response.aread()
                            error_text = error_body.decode('utf-8', errors='replace')
                            logger.error(f"[STREAM-{stream_trace_id}] Error body: {error_text}")
                            
                            # Check if this is a session expired error
                            if 'session expired' in error_text.lower():
                                logger.warning(f"[STREAM-{stream_trace_id}] Detected session expired error, will create new session and retry")
                                retry_with_new_session = True
                                
                                if db_api_key and user:
                                    try:
                                        logger.info(f"[STREAM-{stream_trace_id}] Creating new session to replace expired session")
                                        new_session = await session_service.create_automated_session(
                                            db=db,
                                            api_key_id=db_api_key.id,
                                            user_id=user.id,
                                            requested_model=requested_model
                                        )
                                        new_session_id = new_session.id
                                        logger.info(f"[STREAM-{stream_trace_id}] Created new session: {new_session_id}")
                                        
                                        # Add a small delay to ensure the session is fully registered
                                        logger.info(f"[STREAM-{stream_trace_id}] Adding brief delay to ensure session is registered")
                                        await asyncio.sleep(1.0)
                                    except Exception as e:
                                        logger.error(f"[STREAM-{stream_trace_id}] Failed to create new session: {e}")
                                        retry_with_new_session = False
                            
                            # If not retrying, return error to client
                            if not retry_with_new_session:
                                # Return a formatted error message to the client
                                error_msg = {
                                    "error": {
                                        "message": f"Proxy router error: {error_text}",
                                        "type": "proxy_error",
                                        "status": response.status_code
                                    }
                                }
                                yield f"data: {json.dumps(error_msg)}\n\n".encode('utf-8')
                                return
                        except Exception as read_err:
                            logger.error(f"[STREAM-{stream_trace_id}] Error reading error response: {read_err}")
                            retry_with_new_session = False
                    
                    # If not retrying, process the response normally
                    if not retry_with_new_session:
                        # Check for empty response (Content-Length: 0)
                        content_length = response.headers.get('content-length')
                        if content_length and int(content_length) == 0:
                            logger.warning(f"[STREAM-{stream_trace_id}] Received response with Content-Length: 0")
                            
                            # Log request details for debugging  
                            if req_body_json:
                                msg_count = len(req_body_json.get("messages", []))
                                has_tool_msg = any(msg.get("role") == "tool" for msg in req_body_json.get("messages", []) if isinstance(msg, dict))
                                has_tool_calls = any("tool_calls" in msg for msg in req_body_json.get("messages", []) if isinstance(msg, dict))
                                
                                logger.warning(f"[STREAM-{stream_trace_id}] Request details: message count: {msg_count}, has tool messages: {has_tool_msg}, has tool calls: {has_tool_calls}")
                                
                                # Return a better error message based on the request type
                                if has_tool_msg:
                                    # This is a tool follow-up response that failed
                                    logger.warning(f"[STREAM-{stream_trace_id}] TOOL FOLLOW-UP FAILED: Empty response received for tool result processing")
                                    error_type = "tool_processing_error" 
                                    error_message = "The model returned an empty response when processing your tool results. This may indicate an issue with the tool call format or the session state."
                                    
                                    # Try direct proxy request with different tool formatting as a diagnostic
                                    try:
                                        logger.info(f"[STREAM-{stream_trace_id}] Attempting diagnostic request without API gateway")
                                        
                                        # Build a simplified version of the messages just for testing
                                        test_messages = req_body_json.get("messages", [])
                                        # Remove any special fields that might be causing issues
                                        for msg in test_messages:
                                            if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content") == "":
                                                msg["content"] = None
                                        
                                        test_body = {
                                            "messages": test_messages,
                                            "stream": True
                                        }
                                        if "tools" in req_body_json:
                                            test_body["tools"] = req_body_json["tools"]
                                        
                                        logger.info(f"[STREAM-{stream_trace_id}] Diagnostic request: {json.dumps(test_body)}")
                                        
                                        # Log this diagnostic attempt
                                        logger.warning(f"[STREAM-{stream_trace_id}] Attempted direct diagnostic, check logs for details")
                                        error_message += " A diagnostic attempt was logged for further analysis."
                                    except Exception as diag_err:
                                        logger.error(f"[STREAM-{stream_trace_id}] Error in diagnostic: {diag_err}")
                                else:
                                    error_type = "empty_response_error"
                                    error_message = "The model returned an empty response. This may indicate an issue with the session or model."
                                    
                                # Include session info in error
                                error_msg = {
                                    "error": {
                                        "message": error_message,
                                        "type": error_type,
                                        "session_id": session_id
                                    }
                                }
                                yield f"data: {json.dumps(error_msg)}\n\n".encode('utf-8')
                                return
                            
                        # Track if we've received any chunks
                        has_received_chunks = False
                        
                        # Simple byte streaming
                        async for chunk_bytes in response.aiter_bytes():
                            has_received_chunks = True
                            chunk_count += 1
                            # For debugging, log first few chunks 
                            if chunk_count <= 2:
                                try:
                                    preview = chunk_bytes[:150].decode('utf-8', errors='replace')
                                    logger.info(f"[STREAM-{stream_trace_id}] Chunk {chunk_count} preview: {preview}")
                                except:
                                    logger.info(f"[STREAM-{stream_trace_id}] Chunk {chunk_count} received (binary data)")
                            yield chunk_bytes
                        
                        # If we got a 200 OK but no chunks despite Content-Length not being 0,
                        # this is an unusual situation
                        if not has_received_chunks and (not content_length or int(content_length) > 0):
                            logger.warning(f"[STREAM-{stream_trace_id}] Received 200 OK but no chunks despite Content-Length not 0")
                            
                            # For tool call follow-ups, add specific messaging
                            error_msg = {
                                "error": {
                                    "message": "Expected content but received empty response from model. This usually indicates an issue with the request format or session state.",
                                    "type": "unexpected_empty_response",
                                    "session_id": session_id
                                }
                            }
                            
                            # If this was a tool call response that failed, add helpful diagnostic info
                            if req_body_json and any(msg.get("role") == "tool" for msg in req_body_json.get("messages", []) if isinstance(msg, dict)):
                                error_msg["error"]["message"] = "Tool call processing failed. The model acknowledged the request but returned no content. Try restructuring your tool response format."
                                error_msg["error"]["type"] = "tool_call_processing_failure"
                                
                                # Log more details about the tool response format
                                tool_messages = [msg for msg in req_body_json.get("messages", []) if isinstance(msg, dict) and msg.get("role") == "tool"]
                                if tool_messages:
                                    for tm in tool_messages:
                                        logger.warning(f"[STREAM-{stream_trace_id}] Tool message format: {json.dumps(tm)}")
                            
                            yield f"data: {json.dumps(error_msg)}\n\n".encode('utf-8')
                        
                        logger.info(f"[STREAM-{stream_trace_id}] Stream finished from proxy after {chunk_count} chunks.")

            # If we need to retry with a new session, do that now
            if retry_with_new_session and new_session_id:
                logger.info(f"[STREAM-{stream_trace_id}] Retrying request with new session ID: {new_session_id}")
                
                # Create new endpoint with new session ID
                retry_endpoint = f"{settings.PROXY_ROUTER_URL}/v1/chat/completions?session_id={new_session_id}"
                
                # Update headers with new session ID
                retry_headers = headers.copy()
                retry_headers["session_id"] = new_session_id
                retry_headers["X-Session-ID"] = new_session_id
                
                # Make the retry request
                async with httpx.AsyncClient() as retry_client:
                    async with retry_client.stream("POST", retry_endpoint, content=body, headers=retry_headers, timeout=60.0) as retry_response:
                        logger.info(f"[STREAM-{stream_trace_id}] Retry request returned status: {retry_response.status_code}")
                        
                        if retry_response.status_code != 200:
                            logger.error(f"[STREAM-{stream_trace_id}] Retry request failed: {retry_response.status_code}")
                            error_body = await retry_response.aread()
                            error_text = error_body.decode('utf-8', errors='replace')
                            error_msg = {
                                "error": {
                                    "message": f"Retry after session refresh failed: {error_text}",
                                    "type": "retry_failed",
                                    "status": retry_response.status_code
                                }
                            }
                            yield f"data: {json.dumps(error_msg)}\n\n".encode('utf-8')
                            return
                        
                        # Stream the retry response
                        retry_chunk_count = 0
                        async for chunk_bytes in retry_response.aiter_bytes():
                            retry_chunk_count += 1
                            if retry_chunk_count <= 2:
                                try:
                                    preview = chunk_bytes[:150].decode('utf-8', errors='replace')
                                    logger.info(f"[STREAM-{stream_trace_id}] Retry chunk {retry_chunk_count} preview: {preview}")
                                except:
                                    logger.info(f"[STREAM-{stream_trace_id}] Retry chunk {retry_chunk_count} received (binary data)")
                            yield chunk_bytes
                        
                        logger.info(f"[STREAM-{stream_trace_id}] Retry stream finished after {retry_chunk_count} chunks")

        except Exception as e:
            logger.error(f"[STREAM-{stream_trace_id}] Error in stream_generator: {e}")
            logger.exception(e)  # Log full stack trace
            # Yield a generic error message as bytes
            error_message = f"data: {{\"error\": {{\"message\": \"Error in API gateway streaming: {str(e)}\", \"type\": \"gateway_error\", \"session_id\": \"{session_id}\"}}}}\n\n"
            yield error_message.encode('utf-8')
    
    # Handle request based on streaming preference
    if should_stream:
        # Use streaming response for streaming requests
        return StreamingResponse(
            stream_generator(), 
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive"
            }
        )
    else:
        # For non-streaming requests, make a regular request and return JSON response
        try:
            # First attempt with original session
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    endpoint, 
                    content=body, 
                    headers=headers, 
                    timeout=60.0
                )
                
                # Check if this is a session expired error
                retry_with_new_session = False
                new_session_id = None
                
                # Check response status
                if response.status_code != 200:
                    logger.error(f"[REQ-{request_id}] Proxy router error response: {response.status_code}")
                    try:
                        error_content = response.text
                        
                        # Check if this is a session expired error
                        if 'session expired' in error_content.lower():
                            logger.warning(f"[REQ-{request_id}] Detected session expired error, will create new session and retry")
                            retry_with_new_session = True
                            
                            if db_api_key and user:
                                try:
                                    logger.info(f"[REQ-{request_id}] Creating new session to replace expired session")
                                    new_session = await session_service.create_automated_session(
                                        db=db,
                                        api_key_id=db_api_key.id,
                                        user_id=user.id,
                                        requested_model=requested_model
                                    )
                                    new_session_id = new_session.id
                                    logger.info(f"[REQ-{request_id}] Created new session: {new_session_id}")
                                    
                                    # Add a small delay to ensure the session is fully registered
                                    logger.info(f"[REQ-{request_id}] Adding brief delay to ensure session is registered")
                                    await asyncio.sleep(1.0)
                                except Exception as e:
                                    logger.error(f"[REQ-{request_id}] Failed to create new session: {e}")
                                    retry_with_new_session = False
                        
                        # If not retrying, return error to client
                        if not retry_with_new_session:
                            try:
                                error_json = json.loads(error_content)
                                return JSONResponse(
                                    status_code=response.status_code,
                                    content=error_json
                                )
                            except:
                                return JSONResponse(
                                    status_code=response.status_code,
                                    content={
                                        "error": {
                                            "message": f"Proxy router error: {error_content}",
                                            "type": "proxy_error",
                                            "status": response.status_code
                                        }
                                    }
                                )
                    except Exception as e:
                        logger.error(f"[REQ-{request_id}] Error parsing error response: {e}")
                        retry_with_new_session = False
                        return JSONResponse(
                            status_code=response.status_code,
                            content={
                                "error": {
                                    "message": f"Proxy router error: {response.text}",
                                    "type": "proxy_error",
                                    "status": response.status_code
                                }
                            }
                        )
                
                # If not retrying, return the original response
                if not retry_with_new_session:
                    # Return successful response as JSON
                    return JSONResponse(
                        content=response.json(),
                        status_code=200
                    )
            
            # If we need to retry with a new session, do that now
            if retry_with_new_session and new_session_id:
                logger.info(f"[REQ-{request_id}] Retrying request with new session ID: {new_session_id}")
                
                # Create new endpoint with new session ID
                retry_endpoint = f"{settings.PROXY_ROUTER_URL}/v1/chat/completions?session_id={new_session_id}"
                
                # Update headers with new session ID
                retry_headers = headers.copy()
                retry_headers["session_id"] = new_session_id
                retry_headers["X-Session-ID"] = new_session_id
                
                # Make the retry request
                async with httpx.AsyncClient() as retry_client:
                    retry_response = await retry_client.post(
                        retry_endpoint,
                        content=body,
                        headers=retry_headers,
                        timeout=60.0
                    )
                    
                    if retry_response.status_code != 200:
                        logger.error(f"[REQ-{request_id}] Retry request failed: {retry_response.status_code}")
                        return JSONResponse(
                            status_code=retry_response.status_code,
                            content={
                                "error": {
                                    "message": f"Retry after session refresh failed: {retry_response.text}",
                                    "type": "retry_failed",
                                    "status": retry_response.status_code
                                }
                            }
                        )
                    
                    # Return successful response
                    return JSONResponse(
                        content=retry_response.json(),
                        status_code=200
                    )
                
        except Exception as e:
            logger.error(f"[REQ-{request_id}] Error in non-streaming request: {e}")
            logger.exception(e)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error": {
                        "message": f"Error in API gateway: {str(e)}",
                        "type": "gateway_error",
                        "session_id": session_id
                    }
                }
            ) 