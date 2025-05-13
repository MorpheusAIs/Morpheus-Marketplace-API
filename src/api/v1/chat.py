# Chat routes 

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
    content: str
    name: Optional[str] = None

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
    stream: Optional[bool] = True
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

@router.post("/completions", response_model=None, responses={
    200: {
        "description": "Chat completion response",
        "content": {
            "text/event-stream": {
                "schema": {"type": "string"}
            },
            "application/json": {
                "schema": openai_schemas.ChatCompletionResponse.schema()
            }
        }
    }
})
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
    
    # This 'accept_header' variable will be adjusted for the PROXY request if needed.
    accept_header = request.headers.get("accept", "text/event-stream")
    # logger.info(f"Initial Accept header for proxy consideration: {accept_header}") # Old log, can be removed or updated

    # Check if this is a tool calling request
    json_body = request_data.model_dump(exclude_none=True)
    has_tools = "tools" in json_body and json_body["tools"]

    # Adjust 'stream' flag in json_body and 'accept_header' for the proxy if tools are used
    if has_tools:
        tool_handling_log_prefix = "Tool calling detected. "
        # Force stream=true in body for proxy if tools are used
        # Pydantic model for ChatCompletionRequest defaults stream to True.
        # json_body.get('stream') will be False if client explicitly sent 'stream: false'.
        # It will be True if client sent 'stream: true' or omitted 'stream'.
        # It will be None if client sent 'stream: null' (if exclude_none=True during dump, key might be absent).
        current_stream_setting = json_body.get("stream")
        if current_stream_setting is None: # stream was null or missing after dump
             current_stream_setting = True # Align with Pydantic default for logging

        if not current_stream_setting:
            logger.warning(f"{tool_handling_log_prefix}Client specified 'stream: false'. Forcing 'stream: true' in request to backend proxy due to tool usage.")
        json_body["stream"] = True
        
        # Force accept: text/event-stream for the proxy request if tools are used
        if "text/event-stream" not in accept_header:
            logger.warning(f"{tool_handling_log_prefix}Original Accept header for proxy was '{accept_header}'. Setting to 'text/event-stream' for backend proxy request.")
            accept_header = "text/event-stream"
    # Note: If no tools, 'accept_header' (for proxy) and 'json_body["stream"]' retain client-specified or default values.
    
    # Extract necessary fields
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
                # Check if requested model is different from current session's model
                if requested_model and session.model != requested_model:
                    logger.info(f"Model change detected. Current: {session.model}, Requested: {requested_model}")
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
                        # Fall through to use existing session as fallback
                        session_id = session.id
                        logger.warning(f"Using existing session as fallback: {session_id}")
                else:
                    # Use the session ID from the database - using 'id' instead of 'session_id'
                    session_id = session.id
                    logger.info(f"Using existing session ID from database: {session_id}")
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
                            test_url = f"{settings.PROXY_ROUTER_URL}/health"
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
                        
                        # Session creation failed - use mock session for testing
                        mock_session_id = f"mock-{uuid.uuid4()}"
                        logger.warning(f"Automated session creation failed, using mock session ID: {mock_session_id}")
                        session_id = mock_session_id
                    logger.info("=========== SESSION DEBUG END ===========")
                except Exception as e:
                    # Error in session creation - use mock session for testing
                    mock_session_id = f"mock-{uuid.uuid4()}"
                    logger.error(f"Automated session creation error: {str(e)}")
                    logger.exception(e)  # Log full stack trace
                    logger.warning(f"Using mock session ID for testing: {mock_session_id}")
                    session_id = mock_session_id
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
    
    # Log complete request for debugging when tools are used
    if "tools" in json_body:
        logger.info("===== TOOL CALLING REQUEST =====")
        logger.info(f"Endpoint: {endpoint}")
        logger.info(f"Headers: {json.dumps({k: v for k, v in headers.items() if k != 'authorization'})}")
        logger.info(f"Request body: {json.dumps(json.loads(body.decode('utf-8')), indent=2)}")
        logger.info("================================")
    
    # Check if we're using a mock session ID
    is_mock_session = session_id.startswith("mock-")
    if is_mock_session:
        logger.info(f"Using mock session ID: {session_id} - will handle response directly")
        
    logger.info(f"Making request to {endpoint} with session_id: {session_id}")
    logger.info(f"Using session_id in header, URL parameter, and X-Session-ID header")
    
    # Handle streaming only - assume all requests are streaming
    async def stream_generator():
        stream_trace_id = str(uuid.uuid4())[:8]
        logger.info(f"[STREAM-{stream_trace_id}] Starting stream generator")
        chunk_count = 0
        try:
            # For mock sessions, generate a mock response
            if is_mock_session:
                logger.info(f"[STREAM-{stream_trace_id}] Using mock session ID: {session_id}")
                # Extract the first user message from the request
                messages = json_body.get("messages", [])
                user_message = next((m["content"] for m in messages if m["role"] == "user"), "Hello")
                
                # Generate a simple mock response
                mock_response = {
                    "id": f"chatcmpl-{uuid.uuid4()}",
                    "object": "chat.completion.chunk",
                    "created": int(datetime.now().timestamp()),
                    "model": requested_model or "mistral-31-24b",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "content": f"This is a mock response to: '{user_message}'. The actual LLM service is not available in test mode."
                            },
                            "finish_reason": None
                        }
                    ]
                }
                
                # Yield the mock response as an SSE event
                logger.info(f"[STREAM-{stream_trace_id}] Sending mock response chunk")
                yield f"data: {json.dumps(mock_response)}\n\n".encode('utf-8')
                
                # Add a finish message
                finish_msg = {
                    "id": f"chatcmpl-{uuid.uuid4()}",
                    "object": "chat.completion.chunk",
                    "created": int(datetime.now().timestamp()),
                    "model": requested_model or "mistral-31-24b",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop"
                        }
                    ]
                }
                logger.info(f"[STREAM-{stream_trace_id}] Sending mock finish chunk")
                yield f"data: {json.dumps(finish_msg)}\n\n".encode('utf-8')
                logger.info(f"[STREAM-{stream_trace_id}] Sending mock DONE marker")
                yield "data: [DONE]\n\n".encode('utf-8')
                return
            
            logger.info(f"[STREAM-{stream_trace_id}] Making request to proxy router with streaming")
            async with httpx.AsyncClient() as client:
                # Use streaming request to avoid buffering the whole response
                async with client.stream("POST", endpoint, content=body, headers=headers, timeout=60.0) as response:
                    # Check response status
                    if response.status_code != 200:
                        logger.error(f"[STREAM-{stream_trace_id}] Proxy router error: {response.status_code}")
                        error_msg = f"Proxy router error: {response.status_code}"
                        error_type = "ProxyRouterError"
                        
                        # Try to get more error details from response
                        try:
                            error_text = await response.aread()
                            error_json = json.loads(error_text)
                            if isinstance(error_json, dict):
                                if "error" in error_json:
                                    error_msg = error_json["error"]
                                elif "detail" in error_json:
                                    error_msg = error_json["detail"]
                                elif "message" in error_json:
                                    error_msg = error_json["message"]
                                
                                if "type" in error_json:
                                    error_type = error_json["type"]
                            logger.error(f"[STREAM-{stream_trace_id}] Error details: {error_msg}")
                        except:
                            logger.error(f"[STREAM-{stream_trace_id}] Could not parse error details")
                        
                        # Handle session expired error cases
                        if "session expired" in str(error_msg).lower() or "session not found" in str(error_msg).lower():
                            # Handle session expired error by attempting to create a new session and retry
                            logger.warning(f"[STREAM-{stream_trace_id}] Session {session_id} has expired, attempting to create a new one and retry")
                            try:
                                # Mark the session as inactive in the database
                                await session_crud.mark_session_inactive(db, session_id)
                                
                                # Create a new session with the same model
                                new_session = await session_service.create_automated_session(
                                    db=db,
                                    api_key_id=db_api_key.id,
                                    user_id=user.id,
                                    requested_model=requested_model
                                )
                                
                                if new_session:
                                    new_session_id = new_session.id
                                    logger.info(f"Created new session {new_session_id} after expired session")
                                    
                                    # Small delay to ensure session is registered
                                    await asyncio.sleep(1.0)
                                    
                                    # Update the endpoint with the new session ID
                                    new_endpoint = f"{settings.PROXY_ROUTER_URL}/v1/chat/completions?session_id={new_session_id}"
                                    
                                    # Update headers with new session ID
                                    headers["session_id"] = new_session_id
                                    headers["X-Session-ID"] = new_session_id
                                    
                                    logger.info(f"Retrying request with new session ID: {new_session_id}")
                                    
                                    # Try again with the new session using streaming
                                    async with client.stream("POST", new_endpoint, content=body, headers=headers, timeout=60.0) as retry_response:
                                        if retry_response.status_code == 200:
                                            logger.info("Retry with new session succeeded")
                                            # Process line by line to ensure SSE format for client
                                            tool_calls_found = False
                                            tool_calls_completed = False
                                            async for line_content in retry_response.aiter_lines():
                                                chunk_count += 1
                                                processed_line = line_content.strip()
                                                if not processed_line: # Skip truly empty lines
                                                    continue

                                                logger.info(f"[STREAM-{stream_trace_id}] Processing chunk #{chunk_count}: {processed_line[:100]}...")
                                                
                                                if processed_line.startswith("data:") or (processed_line == "[DONE]" and not processed_line.startswith("data:")):
                                                    if processed_line == "[DONE]" and not processed_line.startswith("data:"):
                                                        logger.info(f"[STREAM-{stream_trace_id}] Received raw [DONE] marker, forwarding as data: [DONE]")
                                                        yield f"data: [DONE]\n\n".encode('utf-8')
                                                    else: # It's already "data: ..."
                                                        # Check if this contains tool calls
                                                        if "tool_calls" in processed_line and not tool_calls_found:
                                                            tool_calls_found = True
                                                            logger.info(f"[STREAM-{stream_trace_id}] First tool call chunk detected")
                                                        
                                                        # Check if this is the last tool call chunk
                                                        if "finish_reason\":\"tool_calls\"" in processed_line:
                                                            tool_calls_completed = True
                                                            logger.info(f"[STREAM-{stream_trace_id}] Tool calls completion chunk detected")
                                                        
                                                        # Log important patterns in chunks
                                                        if "finish_reason\":\"tool_calls\"" in processed_line:
                                                            logger.info(f"[STREAM-{stream_trace_id}] TOOL CALL COMPLETION: {processed_line}")
                                                        elif "tool_calls" in processed_line:
                                                            logger.info(f"[STREAM-{stream_trace_id}] TOOL CALL CHUNK: {processed_line}")
                                                        elif "choices\":[]" in processed_line:
                                                            logger.info(f"[STREAM-{stream_trace_id}] EMPTY CHOICES CHUNK: {processed_line}")
                                                        
                                                        # Special handling for the last chunk with empty choices but with usage
                                                        if "choices\":[]" in processed_line and "usage" in processed_line:
                                                            logger.warning(f"[STREAM-{stream_trace_id}] Empty choices chunk detected after tool calls: {processed_line}")
                                                            
                                                            # If we've already seen tool calls complete, this is likely the problematic chunk
                                                            if tool_calls_completed:
                                                                logger.info(f"[STREAM-{stream_trace_id}] Dropping empty choices chunk after tool calls completion")
                                                                # Skip this chunk entirely - don't even send a DONE here
                                                                continue
                                                            else:
                                                                # If tool calls weren't completed yet, just send DONE
                                                                logger.info(f"[STREAM-{stream_trace_id}] Sending DONE instead of empty choices chunk")
                                                                yield f"data: [DONE]\n\n".encode('utf-8')
                                                        else:
                                                            # Forward the chunk as-is
                                                            logger.info(f"[STREAM-{stream_trace_id}] Forwarding chunk to client")
                                                            yield f"{processed_line}\n\n".encode('utf-8')
                                                            
                                                            # If this is the final tool call chunk, immediately send DONE
                                                            if "finish_reason\":\"tool_calls\"" in processed_line:
                                                                logger.info(f"[STREAM-{stream_trace_id}] Tool calls complete, sending immediate DONE")
                                                                yield f"data: [DONE]\n\n".encode('utf-8')
                                                elif processed_line.startswith("{") and processed_line.endswith("}"):
                                                    try:
                                                        error_json_obj = json.loads(processed_line)
                                                        logger.warning(f"Proxy sent non-SSE JSON on 200 stream (retry path): '{processed_line}'. Wrapping as SSE error.")
                                                        error_payload = {
                                                            "error": {
                                                                "message": error_json_obj.get("error", error_json_obj.get("message", "Error from proxy during stream")),
                                                                "type": error_json_obj.get("type", "ProxyStreamingErrorRetry"),
                                                                "status_code": error_json_obj.get("status_code"),
                                                                "details_raw": processed_line
                                                            }
                                                        }
                                                        yield f"data: {json.dumps(error_payload)}\n\n".encode('utf-8')
                                                    except json.JSONDecodeError:
                                                        logger.warning(f"Proxy sent undecipherable non-SSE, non-JSON line on 200 stream (retry path): '{processed_line}'")
                                                        error_payload = {"error": {"message": "Malformed data from proxy (retry path)", "type": "ProxyMalformedStreamDataRetry", "details_raw": processed_line}}
                                                        yield f"data: {json.dumps(error_payload)}\n\n".encode('utf-8')
                                                else:
                                                    logger.warning(f"Proxy sent an unexpected line on 200 stream (retry path): '{processed_line}'")
                                                    error_payload = {"error": {"message": "Unexpected data from proxy (retry path)", "type": "ProxyUnexpectedStreamDataRetry", "details_raw": processed_line}}
                                                    yield f"data: {json.dumps(error_payload)}\n\n".encode('utf-8')
                                            return
                            except Exception as retry_err:
                                logger.error(f"Failed to retry with new session: {retry_err}")
                        
                        error_payload = json.dumps({
                            "error": {
                                "message": error_msg,
                                "type": error_type,
                                "status_code": response.status_code
                            }
                        })
                        
                        yield f"data: {error_payload}\n\n"
                        return
                    
                    # Process line by line to ensure SSE format for client
                    tool_calls_found = False
                    tool_calls_completed = False
                    async for line_content in response.aiter_lines():
                        chunk_count += 1
                        processed_line = line_content.strip()
                        if not processed_line: # Skip truly empty lines
                            continue

                        logger.info(f"[STREAM-{stream_trace_id}] Processing chunk #{chunk_count}: {processed_line[:100]}...")
                        
                        if processed_line.startswith("data:") or (processed_line == "[DONE]" and not processed_line.startswith("data:")):
                            if processed_line == "[DONE]" and not processed_line.startswith("data:"):
                                logger.info(f"[STREAM-{stream_trace_id}] Received raw [DONE] marker, forwarding as data: [DONE]")
                                yield f"data: [DONE]\n\n".encode('utf-8')
                            else: # It's already "data: ..."
                                # Check if this contains tool calls
                                if "tool_calls" in processed_line and not tool_calls_found:
                                    tool_calls_found = True
                                    logger.info(f"[STREAM-{stream_trace_id}] First tool call chunk detected")
                                
                                # Check if this is the last tool call chunk
                                if "finish_reason\":\"tool_calls\"" in processed_line:
                                    tool_calls_completed = True
                                    logger.info(f"[STREAM-{stream_trace_id}] Tool calls completion chunk detected")
                                
                                # Log important patterns in chunks
                                if "finish_reason\":\"tool_calls\"" in processed_line:
                                    logger.info(f"[STREAM-{stream_trace_id}] TOOL CALL COMPLETION: {processed_line}")
                                elif "tool_calls" in processed_line:
                                    logger.info(f"[STREAM-{stream_trace_id}] TOOL CALL CHUNK: {processed_line}")
                                elif "choices\":[]" in processed_line:
                                    logger.info(f"[STREAM-{stream_trace_id}] EMPTY CHOICES CHUNK: {processed_line}")
                                
                                # Special handling for the last chunk with empty choices but with usage
                                if "choices\":[]" in processed_line and "usage" in processed_line:
                                    logger.warning(f"[STREAM-{stream_trace_id}] Empty choices chunk detected after tool calls: {processed_line}")
                                    
                                    # If we've already seen tool calls complete, this is likely the problematic chunk
                                    if tool_calls_completed:
                                        logger.info(f"[STREAM-{stream_trace_id}] Dropping empty choices chunk after tool calls completion")
                                        # Skip this chunk entirely - don't even send a DONE here
                                        continue
                                    else:
                                        # If tool calls weren't completed yet, just send DONE
                                        logger.info(f"[STREAM-{stream_trace_id}] Sending DONE instead of empty choices chunk")
                                        yield f"data: [DONE]\n\n".encode('utf-8')
                                else:
                                    # Forward the chunk as-is
                                    logger.info(f"[STREAM-{stream_trace_id}] Forwarding chunk to client")
                                    yield f"{processed_line}\n\n".encode('utf-8')
                                    
                                    # If this is the final tool call chunk, immediately send DONE
                                    if "finish_reason\":\"tool_calls\"" in processed_line:
                                        logger.info(f"[STREAM-{stream_trace_id}] Tool calls complete, sending immediate DONE")
                                        yield f"data: [DONE]\n\n".encode('utf-8')
                        elif processed_line.startswith("{") and processed_line.endswith("}"):
                            try:
                                error_json_obj = json.loads(processed_line)
                                logger.warning(f"Proxy sent non-SSE JSON on 200 stream: '{processed_line}'. Wrapping as SSE error.")
                                error_payload = {
                                    "error": {
                                        "message": error_json_obj.get("error", error_json_obj.get("message", "Error from proxy during stream")),
                                        "type": error_json_obj.get("type", "ProxyStreamingError"),
                                        "status_code": error_json_obj.get("status_code"),
                                        "details_raw": processed_line 
                                    }
                                }
                                yield f"data: {json.dumps(error_payload)}\n\n".encode('utf-8')
                            except json.JSONDecodeError:
                                logger.warning(f"Proxy sent undecipherable non-SSE, non-JSON line on 200 stream: '{processed_line}'")
                                error_payload = {"error": {"message": "Malformed data from proxy", "type": "ProxyMalformedStreamData", "details_raw": processed_line}}
                                yield f"data: {json.dumps(error_payload)}\n\n".encode('utf-8')
                        else:
                            logger.warning(f"Proxy sent an unexpected line on 200 stream: '{processed_line}'")
                            error_payload = {"error": {"message": "Unexpected data from proxy", "type": "ProxyUnexpectedStreamData", "details_raw": processed_line}}
                            yield f"data: {json.dumps(error_payload)}\n\n".encode('utf-8')
        except Exception as e:
            logger.error(f"Error in streaming: {e}")
            logger.exception(e)  # Log full stack trace
            yield f"data: {{\"error\": {{\"message\": \"Error in streaming: {str(e)}\", \"type\": \"StreamingError\"}}}}\n\n"
    
    # Return appropriate response based on *original client's* accept header
    if "text/event-stream" in original_client_accept_header:
        return StreamingResponse(
            stream_generator(), # stream_generator uses json_body which has correct 'stream' for proxy
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive"
            }
        )
    else:
        # For application/json, we need to collect the entire response
        async def json_response_generator():
            response_chunks_bytes = []
            async for chunk_bytes in stream_generator():
                response_chunks_bytes.append(chunk_bytes)
            return b''.join(response_chunks_bytes)

        complete_response_bytes = await json_response_generator()
        complete_response_str = complete_response_bytes.decode('utf-8')

        try:
            # Direct fix for empty choices issue
            logger.info("Converting stream to JSON response...")
            
            # Set up combined response structure
            combined_response = None
            last_choices = None
            usage_data = None
            tool_calls_found = False
            
            # Process each line to build a complete response
            for line in complete_response_str.split('\n'):
                line = line.strip()
                if not line or not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                
                try:
                    chunk = json.loads(line[6:])  # Remove "data: " prefix
                    
                    # Skip problematic empty choices chunks that also have usage data
                    if "choices" in chunk and len(chunk["choices"]) == 0 and "usage" in chunk:
                        logger.warning("Skipping empty choices chunk with usage in non-streaming mode")
                        # Only capture the usage data
                        usage_data = chunk.get("usage")
                        continue
                    
                    # Initialize combined response from first chunk
                    if combined_response is None:
                        combined_response = chunk
                    
                    # Update top-level fields from each chunk
                    combined_response["id"] = chunk.get("id", combined_response["id"])
                    combined_response["created"] = chunk.get("created", combined_response["created"])
                    combined_response["model"] = chunk.get("model", combined_response["model"])
                    combined_response["system_fingerprint"] = chunk.get("system_fingerprint", combined_response["system_fingerprint"])
                    
                    # Extract choices if present and not empty
                    if "choices" in chunk and chunk["choices"]:
                        last_choices = chunk["choices"]
                        
                        # Check if this contains tool calls
                        for choice in chunk["choices"]:
                            if "delta" in choice and "tool_calls" in choice["delta"]:
                                tool_calls_found = True
                    
                    # Extract usage if present
                    if "usage" in chunk:
                        usage_data = chunk["usage"]
                    
                except Exception as e:
                    logger.error(f"Error processing chunk: {e}")
            
            # Ensure we have a valid response with choices and usage
            if combined_response:
                # Add last valid choices and usage to the response
                if last_choices:
                    # Convert delta format to message format for final response
                    final_choices = []
                    for choice in last_choices:
                        # Create a complete message structure from deltas
                        final_choice = {
                            "index": choice.get("index", 0),
                            "message": {
                                "role": "assistant",
                                # Tool calls if present
                                "tool_calls": choice.get("delta", {}).get("tool_calls", []) if "delta" in choice else []
                            },
                            "finish_reason": choice.get("finish_reason", "stop"),
                        }
                        
                        # Add content if present
                        if "delta" in choice and "content" in choice["delta"] and choice["delta"]["content"]:
                            final_choice["message"]["content"] = choice["delta"]["content"]
                        
                        final_choices.append(final_choice)
                    
                    combined_response["choices"] = final_choices
                else:
                    # Create a minimal placeholder choice if none found
                    combined_response["choices"] = [{
                        "index": 0,
                        "message": {"role": "assistant", "tool_calls": [] if tool_calls_found else None},
                        "finish_reason": "tool_calls" if tool_calls_found else "stop"
                    }]
                
                # Add usage information if found
                if usage_data:
                    combined_response["usage"] = usage_data
                
                logger.info(f"Successfully constructed JSON response from stream: {json.dumps(combined_response)}")
                return JSONResponse(content=combined_response)
            
            # Fall back to previous implementation if this approach fails
            logger.warning("Direct conversion failed, falling back to chunk-by-chunk processing")
            final_json_response = None
            accumulated_choices = {} # Keyed by choice index

            raw_error_line = None

            for line in complete_response_str.split('\n'):
                if not line.strip():
                    continue

                if line.startswith("data: "):
                    if line[6:] == "[DONE]":
                        continue
                    
                    try:
                        chunk_data = json.loads(line[6:])
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse JSON from SSE chunk: {line[6:]} - Error: {e}")
                        # If a chunk is malformed, we might have to return raw or a more generic error
                        # For now, we'll try to continue if possible, but this might indicate a problem
                        continue


                    if final_json_response is None:
                        final_json_response = {
                            "id": chunk_data.get("id"),
                            "object": "chat.completion", # Changed from chat.completion.chunk
                            "created": chunk_data.get("created"),
                            "model": chunk_data.get("model"),
                            "choices": [],
                            "system_fingerprint": chunk_data.get("system_fingerprint"),
                            # "usage" field is typically not in chunks, would be added by proxy if available
                        }

                    # Update top-level fields if they change (e.g. id, model, created might be same across chunks)
                    if "id" in chunk_data: final_json_response["id"] = chunk_data["id"]
                    if "model" in chunk_data: final_json_response["model"] = chunk_data["model"]
                    if "created" in chunk_data: final_json_response["created"] = chunk_data["created"]
                    if "system_fingerprint" in chunk_data: final_json_response["system_fingerprint"] = chunk_data["system_fingerprint"]


                    for choice_chunk in chunk_data.get("choices", []):
                        idx = choice_chunk["index"]
                        if idx not in accumulated_choices:
                            accumulated_choices[idx] = {
                                "index": idx,
                                "message": {"role": None, "content": None, "tool_calls": []},
                                "finish_reason": None,
                                "content_filter_results": choice_chunk.get("content_filter_results") # take first one
                            }
                        
                        current_choice_acc = accumulated_choices[idx]

                        delta = choice_chunk.get("delta", {})
                        if not delta and "message" in choice_chunk: # Non-delta format (less common in streams)
                            delta = choice_chunk["message"]


                        if "role" in delta and delta["role"]:
                            current_choice_acc["message"]["role"] = delta["role"]
                        
                        if "content" in delta and delta["content"] is not None:
                            if current_choice_acc["message"]["content"] is None:
                                current_choice_acc["message"]["content"] = ""
                            current_choice_acc["message"]["content"] += delta["content"]

                        if "tool_calls" in delta and delta["tool_calls"]:
                            for tool_call_delta in delta["tool_calls"]:
                                tc_idx = tool_call_delta["index"]
                                
                                # Ensure tool_calls list is long enough
                                while len(current_choice_acc["message"]["tool_calls"]) <= tc_idx:
                                    current_choice_acc["message"]["tool_calls"].append({
                                        "id": None, "type": "function", "function": {"name": None, "arguments": ""}
                                    })
                                
                                target_tc = current_choice_acc["message"]["tool_calls"][tc_idx]
                                
                                if "id" in tool_call_delta and tool_call_delta["id"]:
                                    target_tc["id"] = tool_call_delta["id"]
                                if "type" in tool_call_delta and tool_call_delta["type"]:
                                    target_tc["type"] = tool_call_delta["type"]
                                if "function" in tool_call_delta:
                                    if "name" in tool_call_delta["function"] and tool_call_delta["function"]["name"]:
                                        target_tc["function"]["name"] = tool_call_delta["function"]["name"]
                                    if "arguments" in tool_call_delta["function"] and tool_call_delta["function"]["arguments"]:
                                        target_tc["function"]["arguments"] += tool_call_delta["function"]["arguments"]
                        
                        if "finish_reason" in choice_chunk and choice_chunk["finish_reason"]:
                            current_choice_acc["finish_reason"] = choice_chunk["finish_reason"]
                        
                        if "content_filter_results" in choice_chunk: # update if present in later chunks
                            current_choice_acc["content_filter_results"] = choice_chunk["content_filter_results"]


                elif line.strip().startswith("{") and line.strip().endswith("}"):
                    # This might be a raw JSON error line not prefixed by "data:"
                    try:
                        potential_error = json.loads(line)
                        if "error" in potential_error: # Heuristic for an error object
                            logger.warning(f"Found raw JSON error in stream: {line}")
                            raw_error_line = potential_error # Store it
                            # Decide if we should stop processing or try to return what we have + this error
                    except json.JSONDecodeError:
                        logger.warning(f"Found non-SSE line that is not valid JSON: {line}")


            if final_json_response:
                final_json_response["choices"] = sorted(list(accumulated_choices.values()), key=lambda c: c["index"])
                # Clean up message fields that are still None
                for choice in final_json_response["choices"]:
                    if choice["message"]["content"] is None:
                        del choice["message"]["content"]
                    if not choice["message"]["tool_calls"]:
                        del choice["message"]["tool_calls"]
                    elif choice["message"]["role"] is None and "content" not in choice["message"] and not choice["message"].get("tool_calls"): # if role is still None, and no content/tool_calls, make role assistant if applicable
                         # This case needs careful thought based on expected OpenAI behavior.
                         # Usually, a role should be present.
                         pass


                # If we captured a raw error and also processed some data,
                # it's ambiguous. For now, returning processed data if any.
                # If no actual data chunks were processed, and only error, then proxy_router error handling might be better.
                return JSONResponse(content=final_json_response)
            elif raw_error_line: # Only a raw error was found
                 return JSONResponse(content=raw_error_line, status_code=500) # Or status from error if available

            # If we reach here, complete_response_str was empty or only "[DONE]" or unparseable
            logger.warning("Could not form a JSON response from SSE stream.")
            # Fallback to PlainTextResponse with whatever was received, if anything meaningful
            return PlainTextResponse(content=complete_response_str if complete_response_str.strip() else "Empty or invalid SSE stream.", 
                                     media_type="text/plain", status_code=500)

        except Exception as e:
            logger.error(f"Critical error converting SSE to JSON: {e}", exc_info=True)
            # Return the raw response if we can't parse it, including any raw error line
            try:
                # Check if this is the nil error we're seeing
                if "nil" in str(e):
                    logger.warning("Detected nil pointer error - this may be due to empty choices in the final chunk")
                    # Try to construct a valid response from available chunks
                    constructed_response = None
                    usage_info = None
                    tool_calls_detected = False
                    
                    # First try to find usage information
                    for line in complete_response_str.split('\n'):
                        if line.startswith("data: ") and "usage" in line:
                            try:
                                chunk_data = json.loads(line[6:])
                                if "usage" in chunk_data:
                                    usage_info = chunk_data["usage"]
                                    logger.info(f"Found usage info: {json.dumps(usage_info)}")
                                    break
                            except Exception as usage_err:
                                logger.error(f"Error extracting usage info: {usage_err}")
                    
                    # Now find a good base chunk with choices
                    for line in complete_response_str.split('\n'):
                        if line.startswith("data: ") and "choices" in line and "\"choices\":[]" not in line:
                            try:
                                chunk_data = json.loads(line[6:])
                                if "choices" in chunk_data and chunk_data["choices"]:
                                    # Check for tool calls
                                    for choice in chunk_data["choices"]:
                                        if "delta" in choice and "tool_calls" in choice["delta"]:
                                            tool_calls_detected = True
                                    
                                    # Use this as base for response
                                    constructed_response = {
                                        "id": chunk_data.get("id", ""),
                                        "object": "chat.completion",
                                        "created": chunk_data.get("created", int(datetime.now().timestamp())),
                                        "model": chunk_data.get("model", ""),
                                        "choices": chunk_data.get("choices", []),
                                        "system_fingerprint": chunk_data.get("system_fingerprint", "")
                                    }
                                    
                                    # Add usage info if found
                                    if usage_info:
                                        constructed_response["usage"] = usage_info
                                    
                                    # Convert delta format to message format
                                    final_choices = []
                                    for choice in constructed_response["choices"]:
                                        final_choice = {
                                            "index": choice.get("index", 0),
                                            "message": {
                                                "role": "assistant"
                                            },
                                            "finish_reason": "tool_calls" if tool_calls_detected else "stop"
                                        }
                                        
                                        # Add content if present
                                        if "delta" in choice and "content" in choice["delta"]:
                                            final_choice["message"]["content"] = choice["delta"]["content"]
                                        
                                        # Add tool calls if present
                                        if "delta" in choice and "tool_calls" in choice["delta"]:
                                            final_choice["message"]["tool_calls"] = choice["delta"]["tool_calls"]
                                        
                                        final_choices.append(final_choice)
                                    
                                    constructed_response["choices"] = final_choices
                                    
                                    logger.info(f"Successfully constructed valid response from chunks: {json.dumps(constructed_response)}")
                                    return JSONResponse(content=constructed_response)
                            except Exception as chunk_err:
                                logger.error(f"Error processing chunk during nil recovery: {chunk_err}")
                    
                    # If no good chunk with choices found, create a minimal valid response
                    if not constructed_response:
                        # Create minimal valid response
                        min_response = {
                            "id": f"chatcmpl-{uuid.uuid4()}",
                            "object": "chat.completion",
                            "created": int(datetime.now().timestamp()),
                            "model": "unknown",
                            "choices": [{
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "tool_calls": [] if tool_calls_detected else None
                                },
                                "finish_reason": "tool_calls" if tool_calls_detected else "stop"
                            }]
                        }
                        
                        # Add usage if found
                        if usage_info:
                            min_response["usage"] = usage_info
                        
                        logger.info(f"Created minimal valid response: {json.dumps(min_response)}")
                        return JSONResponse(content=min_response)
            except Exception as recovery_err:
                logger.error(f"Error in recovery attempt: {recovery_err}", exc_info=True)
            
            # If all else fails, return the raw response
            logger.error("All recovery attempts failed, returning plain text response")
            return PlainTextResponse(content=complete_response_str, media_type="text/plain", status_code=500) 