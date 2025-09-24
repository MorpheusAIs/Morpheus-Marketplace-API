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
from datetime import datetime
import uuid
import asyncio
import base64
from pydantic import BaseModel, Field

from ...dependencies import get_api_key_user, api_key_header
from ...core.structured_logger import API_LOG

# Setup structured logging (API endpoints category)
chat_log = API_LOG.named("CHAT")
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
    
    # Check system-wide feature flag first
    if not settings.AUTOMATION_FEATURE_ENABLED:
        chat_log.with_fields(
            event_type="automation_disabled",
            scope="system_wide"
        ).info("Automation feature is disabled system-wide")
        return None
        
    # Check if automation is enabled for the user in their settings
    automation_settings = await automation_crud.get_automation_settings(db, user.id)
    
    # If settings don't exist yet, create them with automation enabled by default
    if not automation_settings:
        chat_log.with_fields(
            event_type="automation_settings",
            user_id=user.id,
            action="create_default",
            default_enabled=True
        ).infof("No automation settings found for user %d - creating default settings with automation enabled", user.id)
        automation_settings = await automation_crud.create_automation_settings(
            db=db,
            user_id=user.id,
            is_enabled=True,  # Enable automation by default
            session_duration=3600  # Default 1 hour session
        )
    # If settings exist but automation is disabled, log and return None
    elif not automation_settings.is_enabled:
        chat_log.with_fields(
            event_type="automation_disabled",
            user_id=user.id,
            scope="user_specific"
        ).infof("Automation is explicitly disabled for user %d", user.id)
        return None
    
    # Automation is enabled - create a new session
    chat_log.with_fields(
        event_type="automation_enabled",
        user_id=user.id,
        action="creating_session"
    ).infof("Automation enabled for user %d - creating new session", user.id)
    
    # Create new session with requested model
    session_duration = automation_settings.session_duration
    try:
        chat_log.with_fields(
            event_type="session_creation",
            user_id=user.id,
            requested_model=requested_model,
            session_duration=session_duration,
            automation=True
        ).infof("Attempting to create automated session for user %d with model %s, duration %d", user.id, requested_model, session_duration)
        new_session = await session_service.create_automated_session(
            db=db,
            api_key_id=db_api_key.id,
            user_id=user.id,
            requested_model=requested_model,
            session_duration=session_duration
        )
        session_id = new_session.id
        chat_log.with_fields(
            event_type="session_creation",
            session_id=session_id,
            status="success",
            automation=True
        ).infof("Created new automated session: %s", session_id)
        
        # Add a small delay to ensure the session is fully registered
        chat_log.with_fields(
            event_type="session_delay",
            session_id=session_id
        ).info("Adding a brief delay to ensure session is fully registered")
        await asyncio.sleep(1.0)  # 1 second delay
        return session_id
    except Exception as e:
        chat_log.with_fields(
            event_type="session_creation",
            status="failed",
            error=str(e),
            automation=True
        ).errorf("Error creating automated session: %s", e)
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
    request_id = str(uuid.uuid4())[:8]  # Generate short request ID for tracing
    chat_log.with_fields(
        event_type="chat_request",
        request_id=request_id,
        user_id=user.id
    ).infof("[REQ-%s] New chat completion request received", request_id)
    
    original_client_accept_header = request.headers.get("accept", "text/event-stream")
    chat_log.with_fields(
        event_type="client_headers",
        request_id=request_id,
        accept_header=original_client_accept_header
    ).infof("[REQ-%s] Client's original Accept header: %s", request_id, original_client_accept_header)
    
    # Check if we have a valid user from the API key
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Set up logging for this request
    chat_log.with_fields(
        event_type="request_processing",
        request_id=request_id,
        user_id=user.id
    ).infof("Processing chat completion request for user %d", user.id)
    
    json_body = request_data.model_dump(exclude_none=True)
    has_tools = "tools" in json_body and json_body["tools"]

    # Use the client's stream parameter directly
    # If stream is None (not specified), default to False for consistency
    should_stream = request_data.stream if request_data.stream is not None else False
    
    # Check for tool requests with streaming disabled
    if has_tools and not should_stream:
        chat_log.with_fields(
            event_type="tool_calling_warning",
            request_id=request_id,
            has_tools=has_tools,
            should_stream=should_stream
        ).warnf("[REQ-%s] Tool calling requested with stream=false - this may cause issues with some models", request_id)
        # We'll respect the client's choice, but log a warning
    
    json_body["stream"] = should_stream
    
    # Set accept header based on streaming preference
    if should_stream:
        accept_header = "text/event-stream"
    else:
        accept_header = original_client_accept_header
    
    chat_log.with_fields(
        event_type="request_configuration",
        request_id=request_id,
        original_accept=original_client_accept_header,
        client_stream=should_stream,
        has_tools=has_tools,
        proxy_stream=json_body['stream'],
        proxy_accept_header=accept_header
    ).infof("[REQ-%s] Request configuration - client stream: %s, proxy stream: %s", request_id, should_stream, json_body['stream'])

    # Extract necessary fields that were not part of the core OpenAI payload manipulated above
    session_id = json_body.pop("session_id", None)
    requested_model = json_body.pop("model", None)
    
    # Check if this is a tool calling request and if the model supports it
    if has_tools:
        # List of models known to support tool calling - update this list as needed
        tool_calling_models = ["llama-3.3-70b", "claude-3.5", "claude-3-opus", "gpt-4o", "gpt-4", "mistral-large", "gemini-pro"]
        
        if requested_model and requested_model.lower() not in [m.lower() for m in tool_calling_models]:
            chat_log.with_fields(
                event_type="model_tool_warning",
                request_id=request_id,
                requested_model=requested_model,
                supported_models=tool_calling_models
            ).warnf("Model %s may not support tool calling. Consider using one of: %s", requested_model, ', '.join(tool_calling_models))
    
    # Log tool-related parameters if present (for debugging)
    if "tools" in json_body:
        chat_log.with_fields(
            event_type="tools_included",
            request_id=request_id,
            tools=json_body['tools']
        ).infof("Request includes tools: %s", json.dumps(json_body['tools']))
    if "tool_choice" in json_body:
        chat_log.with_fields(
            event_type="tool_choice_included",
            request_id=request_id,
            tool_choice=json_body['tool_choice']
        ).infof("Request includes tool_choice: %s", json.dumps(json_body['tool_choice']))
    
    body = json.dumps(json_body).encode('utf-8')
    
    # Log the original request details
    chat_log.with_fields(
        event_type="request_details",
        request_id=request_id,
        session_id=session_id,
        requested_model=requested_model
    ).infof("Original request - session_id: %s, model: %s", session_id, requested_model)
    
    # Store API key reference at a higher scope for later use in error handling
    db_api_key = None
    
    # If no session_id from body, try to get from database
    if not session_id and user.api_keys:
        try:
            chat_log.with_fields(
                event_type="session_retrieval",
                request_id=request_id,
                action="retrieve_or_create"
            ).info("No session_id in request, attempting to retrieve or create one")
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
                    chat_log.with_fields(
                        event_type="model_conversion",
                        request_id=request_id,
                        requested_model=requested_model
                    ).infof("Converting requested model '%s' to model ID for comparison", requested_model)
                    try:
                        requested_model_id = await model_router.get_target_model(requested_model)
                        chat_log.with_fields(
                            event_type="model_resolution",
                            request_id=request_id,
                            requested_model=requested_model,
                            resolved_id=requested_model_id
                        ).infof("Requested model '%s' resolved to ID: %s", requested_model, requested_model_id)
                        
                        # First check if the session is expired before comparing models
                        if session.is_expired:
                            chat_log.with_fields(
                                event_type="session_expired",
                                request_id=request_id,
                                session_id=session.id,
                                action="creating_new"
                            ).warnf("Session %s is expired, creating new session regardless of model match", session.id)
                            try:
                                new_session = await session_service.create_automated_session(
                                    db=db,
                                    api_key_id=db_api_key.id,
                                    user_id=user.id,
                                    requested_model=requested_model
                                )
                                session_id = new_session.id
                                chat_log.with_fields(
                                    event_type="session_replacement",
                                    request_id=request_id,
                                    old_session_id=session.id,
                                    new_session_id=session_id,
                                    reason="expired"
                                ).infof("Created new session to replace expired session: %s", session_id)
                                
                                # Add a small delay to ensure the session is fully registered
                                chat_log.with_fields(
                                    event_type="session_delay",
                                    request_id=request_id,
                                    session_id=session_id,
                                    context="expired_session_replacement"
                                ).info("Adding a brief delay to ensure session is fully registered")
                                await asyncio.sleep(1.0)  # 1 second delay
                            except Exception as e:
                                chat_log.with_fields(
                                    event_type="session_replacement",
                                    request_id=request_id,
                                    status="failed",
                                    error=str(e)
                                ).errorf("Error creating new session to replace expired session: %s", e)
                                raise HTTPException(
                                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail=f"Failed to create new session to replace expired one: {e}"
                                )
                        # Compare model IDs (hash to hash) only for non-expired sessions
                        elif session.model != requested_model_id:
                            chat_log.with_fields(
                                event_type="model_change_detected",
                                request_id=request_id,
                                current_model=session.model,
                                requested_model=requested_model_id,
                                session_id=session.id
                            ).infof("Model change detected. Current: %s, Requested: %s", session.model, requested_model_id)
                            chat_log.with_fields(
                                event_type="model_switch",
                                request_id=request_id,
                                action="closing_and_creating"
                            ).info("Switching models by closing current session and creating new one")
                            
                            # Switch to the new model
                            try:
                                new_session = await session_service.switch_model(
                                    db=db,
                                    api_key_id=db_api_key.id,
                                    user_id=user.id,
                                    new_model=requested_model
                                )
                                session_id = new_session.id
                                chat_log.with_fields(
                                    event_type="model_switch",
                                    request_id=request_id,
                                    new_session_id=session_id,
                                    status="success"
                                ).infof("Successfully switched to new model with session: %s", session_id)
                                
                                # Add a small delay to ensure the session is fully registered
                                chat_log.with_fields(
                                    event_type="session_delay",
                                    request_id=request_id,
                                    session_id=session_id,
                                    context="model_switch"
                                ).info("Adding a brief delay to ensure session is fully registered")
                                await asyncio.sleep(1.0)  # 1 second delay
                            except Exception as e:
                                chat_log.with_fields(
                                    event_type="model_switch",
                                    request_id=request_id,
                                    status="failed",
                                    error=str(e)
                                ).errorf("Error switching models: %s", e)
                                # Create a new session instead of falling back to the expired one
                                try:
                                    chat_log.with_fields(
                                        event_type="session_creation_fallback",
                                        request_id=request_id,
                                        reason="switch_model_failure"
                                    ).info("Creating new session after switch_model failure")
                                    new_session = await session_service.create_automated_session(
                                        db=db,
                                        api_key_id=db_api_key.id,
                                        user_id=user.id,
                                        requested_model=requested_model
                                    )
                                    session_id = new_session.id
                                    chat_log.with_fields(
                                        event_type="session_creation_fallback",
                                        request_id=request_id,
                                        new_session_id=session_id,
                                        status="success"
                                    ).infof("Created new replacement session: %s", session_id)
                                    await asyncio.sleep(1.0)  # Small delay to ensure registration
                                except Exception as new_err:
                                    chat_log.with_fields(
                                        event_type="session_creation_fallback",
                                        request_id=request_id,
                                        status="failed",
                                        error=str(new_err)
                                    ).errorf("Failed to create new session after switch failure: %s", new_err)
                                    raise HTTPException(
                                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                        detail=f"Failed to create new session after model switch failure: {new_err}"
                                    )
                        else:
                            # Models match, use existing session
                            session_id = session.id
                            chat_log.with_fields(
                                event_type="session_reuse",
                                request_id=request_id,
                                session_id=session_id,
                                model_id=requested_model_id,
                                reason="models_match"
                            ).infof("Models match (ID: %s), reusing existing session ID: %s", requested_model_id, session_id)
                    except Exception as e:
                        chat_log.with_fields(
                            event_type="model_resolution_error",
                            request_id=request_id,
                            requested_model=requested_model,
                            error=str(e)
                        ).errorf("Error resolving model ID for '%s': %s", requested_model, e)
                        # Fall back to using existing session
                        session_id = session.id
                        chat_log.with_fields(
                            event_type="session_fallback",
                            request_id=request_id,
                            session_id=session_id,
                            reason="model_resolution_error"
                        ).infof("Using existing session ID due to model resolution error: %s", session_id)
                else:
                    # No requested model specified, use existing session
                    session_id = session.id
                    chat_log.with_fields(
                        event_type="session_reuse",
                        request_id=request_id,
                        session_id=session_id,
                        reason="no_model_requested"
                    ).infof("No specific model requested, reusing existing session ID: %s", session_id)
            else:
                chat_log.with_fields(
                    event_type="session_creation_needed",
                    request_id=request_id,
                    reason="no_active_session"
                ).info("No active session found, attempting automated session creation")
                # No active session - try automated session creation
                try:
                    # Add detailed debugging
                    chat_log.with_fields(
                        event_type="session_debug",
                        request_id=request_id,
                        api_key_id=db_api_key.id,
                        user_id=user.id,
                        requested_model=requested_model,
                        proxy_router_url=settings.PROXY_ROUTER_URL
                    ).info("SESSION DEBUG START - Attempting automated session creation")
                    
                    # Test connection to proxy router before session creation
                    try:
                        async with httpx.AsyncClient() as test_client:
                            test_url = f"{settings.PROXY_ROUTER_URL}/healthcheck"
                            chat_log.with_fields(
                                event_type="proxy_health_check",
                                request_id=request_id,
                                test_url=test_url
                            ).infof("Testing connection to proxy router at: %s", test_url)
                            test_response = await test_client.get(test_url, timeout=5.0)
                            chat_log.with_fields(
                                event_type="proxy_health_check",
                                request_id=request_id,
                                status_code=test_response.status_code
                            ).infof("Proxy router health check status: %d", test_response.status_code)
                            if test_response.status_code == 200:
                                chat_log.with_fields(
                                    event_type="proxy_health_check",
                                    request_id=request_id,
                                    response_preview=test_response.text[:100]
                                ).infof("Proxy router health response: %s", test_response.text[:100])
                            else:
                                chat_log.with_fields(
                                    event_type="proxy_health_check",
                                    request_id=request_id,
                                    status_code=test_response.status_code,
                                    response_preview=test_response.text[:100],
                                    status="unhealthy"
                                ).errorf("Proxy router appears unhealthy: %d - %s", test_response.status_code, test_response.text[:100])
                    except Exception as health_err:
                        chat_log.with_fields(
                            event_type="proxy_health_check",
                            request_id=request_id,
                            status="connection_failed",
                            error=str(health_err)
                        ).errorf("Failed to connect to proxy router health endpoint: %s", str(health_err))
                    
                    # Now attempt session creation
                    automated_session = await session_service.create_automated_session(
                        db=db,
                        api_key_id=db_api_key.id,
                        user_id=user.id,
                        requested_model=requested_model
                    )
                    
                    chat_log.with_fields(
                        event_type="session_creation_result",
                        request_id=request_id,
                        automated_session=str(automated_session)
                    ).infof("create_automated_session returned: %s", automated_session)
                    if automated_session:
                        # The Session model uses 'id' attribute, not 'session_id'
                        session_id = automated_session.id
                        chat_log.with_fields(
                            event_type="session_creation",
                            request_id=request_id,
                            session_id=session_id,
                            status="success",
                            automation=True
                        ).infof("Successfully created automated session with ID: %s", session_id)
                        
                        # Add a small delay to ensure the session is fully registered
                        chat_log.with_fields(
                            event_type="session_delay",
                            request_id=request_id,
                            session_id=session_id,
                            context="automated_session_creation"
                        ).info("Adding a brief delay to ensure session is fully registered")
                        await asyncio.sleep(1.0)  # 1 second delay
                    else:
                        # Session creation returned None - generate detailed log
                        chat_log.with_fields(
                            event_type="session_creation",
                            request_id=request_id,
                            status="failed",
                            reason="service_returned_none"
                        ).error("Session service returned None from create_automated_session")
                        chat_log.with_fields(
                            event_type="proxy_availability_check",
                            request_id=request_id,
                            requested_model=requested_model
                        ).info("Checking if proxy router is available for the requested model")
                        
                        try:
                            async with httpx.AsyncClient() as model_client:
                                model_url = f"{settings.PROXY_ROUTER_URL}/v1/models"
                                chat_log.with_fields(
                                    event_type="models_api_check",
                                    request_id=request_id,
                                    model_url=model_url
                                ).infof("Checking available models at: %s", model_url)
                                model_auth = {
                                    "authorization": f"Basic {base64.b64encode(f'{settings.PROXY_ROUTER_USERNAME}:{settings.PROXY_ROUTER_PASSWORD}'.encode()).decode()}"
                                }
                                model_response = await model_client.get(model_url, headers=model_auth, timeout=5.0)
                                chat_log.with_fields(
                                    event_type="models_api_check",
                                    request_id=request_id,
                                    status_code=model_response.status_code
                                ).infof("Models API status: %d", model_response.status_code)
                                if model_response.status_code == 200:
                                    models_data = model_response.json()
                                    chat_log.with_fields(
                                        event_type="models_api_check",
                                        request_id=request_id,
                                        models_data=models_data
                                    ).infof("Available models: %s", json.dumps(models_data))
                                    # Check if requested model is in the list
                                    model_names = [m.get('id', '') for m in models_data.get('data', [])]
                                    if requested_model in model_names:
                                        chat_log.with_fields(
                                            event_type="model_availability",
                                            request_id=request_id,
                                            requested_model=requested_model,
                                            status="available"
                                        ).infof("Requested model '%s' is available in proxy router", requested_model)
                                    else:
                                        chat_log.with_fields(
                                            event_type="model_availability",
                                            request_id=request_id,
                                            requested_model=requested_model,
                                            available_models=model_names,
                                            status="not_found"
                                        ).errorf("Requested model '%s' NOT found in available models: %s", requested_model, model_names)
                                else:
                                    chat_log.with_fields(
                                        event_type="models_api_check",
                                        request_id=request_id,
                                        status="failed",
                                        response_preview=model_response.text[:200]
                                    ).errorf("Failed to get models: %s", model_response.text[:200])
                        except Exception as model_err:
                            chat_log.with_fields(
                                event_type="models_api_check",
                                request_id=request_id,
                                status="error",
                                error=str(model_err)
                            ).errorf("Error checking models API: %s", str(model_err))
                        
                        # Session creation failed
                        chat_log.with_fields(
                            event_type="session_creation",
                            request_id=request_id,
                            status="failed",
                            automation=True
                        ).error("Automated session creation failed.")
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Automated session creation failed. The model provider may be unavailable."
                        )
                    chat_log.with_fields(
                        event_type="session_debug",
                        request_id=request_id
                    ).info("SESSION DEBUG END")
                except Exception as e:
                    # Error in session creation
                    chat_log.with_fields(
                        event_type="session_creation",
                        request_id=request_id,
                        status="error",
                        error=str(e),
                        automation=True
                    ).errorf("Automated session creation error: %s", str(e))
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"An unexpected error occurred during session creation: {e}"
                    )
        except HTTPException as http_exc:
            # Re-raise HTTP exceptions with logging
            chat_log.with_fields(
                event_type="session_handling",
                request_id=request_id,
                status="http_exception",
                error=http_exc.detail
            ).errorf("HTTP exception during session handling: %s", http_exc.detail)
            raise
        except Exception as e:
            chat_log.with_fields(
                event_type="session_handling",
                request_id=request_id,
                status="error",
                error=str(e)
            ).errorf("Error in session handling: %s", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error handling session: {str(e)}"
            )
    
    # If we still don't have a session_id, return an error
    if not session_id:
        chat_log.with_fields(
            event_type="session_handling",
            request_id=request_id,
            status="no_session_id"
        ).error("No session ID after all attempts")
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
                chat_log.with_fields(
                    event_type="tool_choice_fix",
                    request_id=request_id,
                    original_structure=json_body['tool_choice']
                ).warnf("Found nested tool_choice, fixing structure: %s", json.dumps(json_body['tool_choice']))
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
                        chat_log.with_fields(
                            event_type="tool_choice_fix",
                            request_id=request_id,
                            fixed_structure=json_body['tool_choice'],
                            status="success"
                        ).infof("Fixed tool_choice to: %s", json.dumps(json_body['tool_choice']))
                except Exception as e:
                    chat_log.with_fields(
                        event_type="tool_choice_fix",
                        request_id=request_id,
                        status="error",
                        error=str(e)
                    ).errorf("Error fixing tool_choice: %s", str(e))

    # Additional fix for tool_choice within tools parameters
    if "tools" in json_body:
        for i, tool in enumerate(json_body["tools"]):
            if isinstance(tool, dict) and "function" in tool:
                func = tool["function"]
                if isinstance(func, dict) and "parameters" in func:
                    params = func["parameters"]
                    if isinstance(params, dict) and "tool_choice" in params:
                        chat_log.with_fields(
                            event_type="tool_parameter_cleanup",
                            request_id=request_id,
                            tool_name=func.get('name'),
                            removed_param=params['tool_choice']
                        ).warnf("Found tool_choice in tool parameters, removing: %s", json.dumps(params['tool_choice']))
                        # Remove tool_choice from parameters
                        del json_body["tools"][i]["function"]["parameters"]["tool_choice"]
                        chat_log.with_fields(
                            event_type="tool_parameter_cleanup",
                            request_id=request_id,
                            tool_name=func.get('name'),
                            status="success"
                        ).infof("Removed tool_choice from tool parameters for tool: %s", func.get('name'))

    # Special handling for message with tool_calls and empty content
    if "messages" in json_body:
        for i, msg in enumerate(json_body["messages"]):
            if isinstance(msg, dict) and msg.get("role") == "assistant" and "tool_calls" in msg:
                if msg.get("content") == "":
                    chat_log.with_fields(
                        event_type="message_cleanup",
                        request_id=request_id,
                        message_index=i,
                        action="set_null_content"
                    ).infof("Setting null content for assistant message with tool_calls at index %d", i)
                    json_body["messages"][i]["content"] = None

    # Log complete request for debugging when tools are used
    has_tools = "tools" in json_body
    has_tool_messages = False
    if "messages" in json_body:
        has_tool_messages = any(msg.get("role") == "tool" for msg in json_body["messages"] if isinstance(msg, dict))

    if has_tools or has_tool_messages:
        chat_log.with_fields(
            event_type="tool_calling_request",
            request_id=request_id,
            endpoint=endpoint,
            headers={k: v for k, v in headers.items() if k.lower() != 'authorization'},
            request_body=json_body,
            session_id=session_id
        ).info("TOOL CALLING REQUEST - Full request details logged")
    
    # Handle streaming only - assume all requests are streaming
    async def stream_generator():
        stream_trace_id = str(uuid.uuid4())[:8]
        chat_log.with_fields(
            event_type="stream_start",
            request_id=request_id,
            stream_trace_id=stream_trace_id,
            session_id=session_id
        ).infof("[STREAM-%s] Starting stream generator for session: %s", stream_trace_id, session_id)
        chunk_count = 0
        req_body_json = None
        
        try:
            # Parse the request body for debugging - do this before the request
            try:
                req_body_json = json.loads(body.decode('utf-8'))
                has_tool_msg = any(msg.get("role") == "tool" for msg in req_body_json.get("messages", []) if isinstance(msg, dict))
                has_tool_calls = any("tool_calls" in msg for msg in req_body_json.get("messages", []) if isinstance(msg, dict))
                
                if has_tool_msg or has_tool_calls:
                    chat_log.with_fields(
                        event_type="stream_request_analysis",
                        request_id=request_id,
                        stream_trace_id=stream_trace_id,
                        has_tool_messages=has_tool_msg,
                        has_tool_calls=has_tool_calls
                    ).infof("[STREAM-%s] Request contains tool messages: %s, tool calls: %s", stream_trace_id, has_tool_msg, has_tool_calls)
            except Exception as parse_err:
                chat_log.with_fields(
                    event_type="stream_request_analysis",
                    request_id=request_id,
                    stream_trace_id=stream_trace_id,
                    status="parse_error",
                    error=str(parse_err)
                ).errorf("[STREAM-%s] Failed to parse request body: %s", stream_trace_id, parse_err)
            
            # First attempt with existing session
            chat_log.with_fields(
                event_type="stream_proxy_request",
                request_id=request_id,
                stream_trace_id=stream_trace_id,
                endpoint=endpoint
            ).infof("[STREAM-%s] Making request to proxy router: %s", stream_trace_id, endpoint)
            
            # Track if we need to retry due to expired session
            retry_with_new_session = False
            new_session_id = None
            
            async with httpx.AsyncClient() as client:
                async with client.stream("POST", endpoint, content=body, headers=headers, timeout=60.0) as response:
                    # Log proxy status
                    chat_log.with_fields(
                        event_type="stream_proxy_response",
                        request_id=request_id,
                        stream_trace_id=stream_trace_id,
                        status_code=response.status_code,
                        response_headers=dict(response.headers.items())
                    ).infof("[STREAM-%s] Proxy router responded with status: %d", stream_trace_id, response.status_code)
                    
                    if response.status_code != 200:
                        chat_log.with_fields(
                            event_type="stream_proxy_error",
                            request_id=request_id,
                            stream_trace_id=stream_trace_id,
                            status_code=response.status_code
                        ).errorf("[STREAM-%s] Proxy router error response: %d", stream_trace_id, response.status_code)
                        # Try to read and log the error body
                        try:
                            error_body = await response.aread()
                            error_text = error_body.decode('utf-8', errors='replace')
                            chat_log.with_fields(
                                event_type="stream_proxy_error",
                                request_id=request_id,
                                stream_trace_id=stream_trace_id,
                                error_body=error_text
                            ).errorf("[STREAM-%s] Error body: %s", stream_trace_id, error_text)
                            
                            # Check if this is a session expired error
                            if 'session expired' in error_text.lower():
                                chat_log.with_fields(
                                    event_type="stream_session_expired",
                                    request_id=request_id,
                                    stream_trace_id=stream_trace_id,
                                    action="retry_with_new_session"
                                ).warnf("[STREAM-%s] Detected session expired error, will create new session and retry", stream_trace_id)
                                retry_with_new_session = True
                                
                                if db_api_key and user:
                                    try:
                                        chat_log.with_fields(
                                            event_type="stream_session_replacement",
                                            request_id=request_id,
                                            stream_trace_id=stream_trace_id,
                                            action="creating"
                                        ).infof("[STREAM-%s] Creating new session to replace expired session", stream_trace_id)
                                        new_session = await session_service.create_automated_session(
                                            db=db,
                                            api_key_id=db_api_key.id,
                                            user_id=user.id,
                                            requested_model=requested_model
                                        )
                                        new_session_id = new_session.id
                                        chat_log.with_fields(
                                            event_type="stream_session_replacement",
                                            request_id=request_id,
                                            stream_trace_id=stream_trace_id,
                                            new_session_id=new_session_id,
                                            status="success"
                                        ).infof("[STREAM-%s] Created new session: %s", stream_trace_id, new_session_id)
                                        
                                        # Add a small delay to ensure the session is fully registered
                                        chat_log.with_fields(
                                            event_type="stream_session_delay",
                                            request_id=request_id,
                                            stream_trace_id=stream_trace_id,
                                            new_session_id=new_session_id
                                        ).infof("[STREAM-%s] Adding brief delay to ensure session is registered", stream_trace_id)
                                        await asyncio.sleep(1.0)
                                    except Exception as e:
                                        chat_log.with_fields(
                                            event_type="stream_session_replacement",
                                            request_id=request_id,
                                            stream_trace_id=stream_trace_id,
                                            status="failed",
                                            error=str(e)
                                        ).errorf("[STREAM-%s] Failed to create new session: %s", stream_trace_id, e)
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
                            chat_log.with_fields(
                                event_type="stream_error_handling",
                                request_id=request_id,
                                stream_trace_id=stream_trace_id,
                                error=str(read_err)
                            ).errorf("[STREAM-%s] Error reading error response: %s", stream_trace_id, read_err)
                            retry_with_new_session = False
                    
                    # If not retrying, process the response normally
                    if not retry_with_new_session:
                        # Check for empty response (Content-Length: 0)
                        content_length = response.headers.get('content-length')
                        if content_length and int(content_length) == 0:
                            chat_log.with_fields(
                                event_type="stream_response_warning",
                                request_id=request_id,
                                stream_trace_id=stream_trace_id,
                                content_length=0
                            ).warnf("[STREAM-%s] Received response with Content-Length: 0", stream_trace_id)
                            
                            # Log request details for debugging  
                            if req_body_json:
                                msg_count = len(req_body_json.get("messages", []))
                                has_tool_msg = any(msg.get("role") == "tool" for msg in req_body_json.get("messages", []) if isinstance(msg, dict))
                                has_tool_calls = any("tool_calls" in msg for msg in req_body_json.get("messages", []) if isinstance(msg, dict))
                                
                                chat_log.with_fields(
                                    event_type="stream_request_details",
                                    request_id=request_id,
                                    stream_trace_id=stream_trace_id,
                                    message_count=msg_count,
                                    has_tool_messages=has_tool_msg,
                                    has_tool_calls=has_tool_calls
                                ).warnf("[STREAM-%s] Request details: message count: %d, has tool messages: %s, has tool calls: %s", stream_trace_id, msg_count, has_tool_msg, has_tool_calls)
                                
                                # Return a better error message based on the request type
                                if has_tool_msg:
                                    # This is a tool follow-up response that failed
                                    chat_log.with_fields(
                                        event_type="stream_tool_followup",
                                        request_id=request_id,
                                        stream_trace_id=stream_trace_id,
                                        status="failed",
                                        reason="empty_response"
                                    ).warnf("[STREAM-%s] TOOL FOLLOW-UP FAILED: Empty response received for tool result processing", stream_trace_id)
                                    error_type = "tool_processing_error" 
                                    error_message = "The model returned an empty response when processing your tool results. This may indicate an issue with the tool call format or the session state."
                                    
                                    # Try direct proxy request with different tool formatting as a diagnostic
                                    try:
                                        chat_log.with_fields(
                                            event_type="stream_diagnostic",
                                            request_id=request_id,
                                            stream_trace_id=stream_trace_id,
                                            action="direct_request"
                                        ).infof("[STREAM-%s] Attempting diagnostic request without API gateway", stream_trace_id)
                                        
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
                                        
                                        chat_log.with_fields(
                                            event_type="stream_diagnostic",
                                            request_id=request_id,
                                            stream_trace_id=stream_trace_id,
                                            diagnostic_body=test_body
                                        ).infof("[STREAM-%s] Diagnostic request: %s", stream_trace_id, json.dumps(test_body))
                                        
                                        # Log this diagnostic attempt
                                        chat_log.with_fields(
                                            event_type="stream_diagnostic",
                                            request_id=request_id,
                                            stream_trace_id=stream_trace_id,
                                            status="completed"
                                        ).warnf("[STREAM-%s] Attempted direct diagnostic, check logs for details", stream_trace_id)
                                        error_message += " A diagnostic attempt was logged for further analysis."
                                    except Exception as diag_err:
                                        chat_log.with_fields(
                                            event_type="stream_diagnostic",
                                            request_id=request_id,
                                            stream_trace_id=stream_trace_id,
                                            status="error",
                                            error=str(diag_err)
                                        ).errorf("[STREAM-%s] Error in diagnostic: %s", stream_trace_id, diag_err)
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
                                    chat_log.with_fields(
                                        event_type="stream_chunk",
                                        request_id=request_id,
                                        stream_trace_id=stream_trace_id,
                                        chunk_count=chunk_count,
                                        chunk_preview=preview
                                    ).infof("[STREAM-%s] Chunk %d preview: %s", stream_trace_id, chunk_count, preview)
                                except:
                                    chat_log.with_fields(
                                        event_type="stream_chunk",
                                        request_id=request_id,
                                        stream_trace_id=stream_trace_id,
                                        chunk_count=chunk_count,
                                        data_type="binary"
                                    ).infof("[STREAM-%s] Chunk %d received (binary data)", stream_trace_id, chunk_count)
                            yield chunk_bytes
                        
                        # If we got a 200 OK but no chunks despite Content-Length not being 0,
                        # this is an unusual situation
                        if not has_received_chunks and (not content_length or int(content_length) > 0):
                            chat_log.with_fields(
                                event_type="stream_response_warning",
                                request_id=request_id,
                                stream_trace_id=stream_trace_id,
                                issue="no_chunks_despite_content_length"
                            ).warnf("[STREAM-%s] Received 200 OK but no chunks despite Content-Length not 0", stream_trace_id)
                            
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
                                        chat_log.with_fields(
                                            event_type="stream_tool_message",
                                            request_id=request_id,
                                            stream_trace_id=stream_trace_id,
                                            tool_message=tm
                                        ).warnf("[STREAM-%s] Tool message format: %s", stream_trace_id, json.dumps(tm))
                            
                            yield f"data: {json.dumps(error_msg)}\n\n".encode('utf-8')
                        
                        chat_log.with_fields(
                            event_type="stream_complete",
                            request_id=request_id,
                            stream_trace_id=stream_trace_id,
                            total_chunks=chunk_count
                        ).infof("[STREAM-%s] Stream finished from proxy after %d chunks.", stream_trace_id, chunk_count)

            # If we need to retry with a new session, do that now
            if retry_with_new_session and new_session_id:
                chat_log.with_fields(
                    event_type="stream_retry",
                    request_id=request_id,
                    stream_trace_id=stream_trace_id,
                    new_session_id=new_session_id
                ).infof("[STREAM-%s] Retrying request with new session ID: %s", stream_trace_id, new_session_id)
                
                # Create new endpoint with new session ID
                retry_endpoint = f"{settings.PROXY_ROUTER_URL}/v1/chat/completions?session_id={new_session_id}"
                
                # Update headers with new session ID
                retry_headers = headers.copy()
                retry_headers["session_id"] = new_session_id
                retry_headers["X-Session-ID"] = new_session_id
                
                # Make the retry request
                async with httpx.AsyncClient() as retry_client:
                    async with retry_client.stream("POST", retry_endpoint, content=body, headers=retry_headers, timeout=60.0) as retry_response:
                        chat_log.with_fields(
                            event_type="stream_retry_response",
                            request_id=request_id,
                            stream_trace_id=stream_trace_id,
                            retry_status_code=retry_response.status_code
                        ).infof("[STREAM-%s] Retry request returned status: %d", stream_trace_id, retry_response.status_code)
                        
                        if retry_response.status_code != 200:
                            chat_log.with_fields(
                                event_type="stream_retry_response",
                                request_id=request_id,
                                stream_trace_id=stream_trace_id,
                                retry_status_code=retry_response.status_code,
                                status="failed"
                            ).errorf("[STREAM-%s] Retry request failed: %d", stream_trace_id, retry_response.status_code)
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
                                    chat_log.with_fields(
                                        event_type="stream_retry_chunk",
                                        request_id=request_id,
                                        stream_trace_id=stream_trace_id,
                                        retry_chunk_count=retry_chunk_count,
                                        chunk_preview=preview
                                    ).infof("[STREAM-%s] Retry chunk %d preview: %s", stream_trace_id, retry_chunk_count, preview)
                                except:
                                    chat_log.with_fields(
                                        event_type="stream_retry_chunk",
                                        request_id=request_id,
                                        stream_trace_id=stream_trace_id,
                                        retry_chunk_count=retry_chunk_count,
                                        data_type="binary"
                                    ).infof("[STREAM-%s] Retry chunk %d received (binary data)", stream_trace_id, retry_chunk_count)
                            yield chunk_bytes
                        
                        chat_log.with_fields(
                            event_type="stream_retry_complete",
                            request_id=request_id,
                            stream_trace_id=stream_trace_id,
                            retry_chunks=retry_chunk_count
                        ).infof("[STREAM-%s] Retry stream finished after %d chunks", stream_trace_id, retry_chunk_count)

        except Exception as e:
            chat_log.with_fields(
                event_type="stream_error",
                request_id=request_id,
                stream_trace_id=stream_trace_id,
                error=str(e)
            ).errorf("[STREAM-%s] Error in stream_generator: %s", stream_trace_id, e)
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
                    chat_log.with_fields(
                        event_type="non_stream_proxy_error",
                        request_id=request_id,
                        status_code=response.status_code
                    ).errorf("[REQ-%s] Proxy router error response: %d", request_id, response.status_code)
                    try:
                        error_content = response.text
                        
                        # Check if this is a session expired error
                        if 'session expired' in error_content.lower():
                            chat_log.with_fields(
                                event_type="non_stream_session_expired",
                                request_id=request_id,
                                action="retry_with_new_session"
                            ).warnf("[REQ-%s] Detected session expired error, will create new session and retry", request_id)
                            retry_with_new_session = True
                            
                            if db_api_key and user:
                                try:
                                    chat_log.with_fields(
                                        event_type="non_stream_session_replacement",
                                        request_id=request_id,
                                        action="creating"
                                    ).infof("[REQ-%s] Creating new session to replace expired session", request_id)
                                    new_session = await session_service.create_automated_session(
                                        db=db,
                                        api_key_id=db_api_key.id,
                                        user_id=user.id,
                                        requested_model=requested_model
                                    )
                                    new_session_id = new_session.id
                                    chat_log.with_fields(
                                        event_type="non_stream_session_replacement",
                                        request_id=request_id,
                                        new_session_id=new_session_id,
                                        status="success"
                                    ).infof("[REQ-%s] Created new session: %s", request_id, new_session_id)
                                    
                                    # Add a small delay to ensure the session is fully registered
                                    chat_log.with_fields(
                                        event_type="non_stream_session_delay",
                                        request_id=request_id,
                                        new_session_id=new_session_id
                                    ).infof("[REQ-%s] Adding brief delay to ensure session is registered", request_id)
                                    await asyncio.sleep(1.0)
                                except Exception as e:
                                    chat_log.with_fields(
                                        event_type="non_stream_session_replacement",
                                        request_id=request_id,
                                        status="failed",
                                        error=str(e)
                                    ).errorf("[REQ-%s] Failed to create new session: %s", request_id, e)
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
                        chat_log.with_fields(
                            event_type="non_stream_error_parsing",
                            request_id=request_id,
                            error=str(e)
                        ).errorf("[REQ-%s] Error parsing error response: %s", request_id, e)
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
                chat_log.with_fields(
                    event_type="non_stream_retry",
                    request_id=request_id,
                    new_session_id=new_session_id
                ).infof("[REQ-%s] Retrying request with new session ID: %s", request_id, new_session_id)
                
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
                        chat_log.with_fields(
                            event_type="non_stream_retry_response",
                            request_id=request_id,
                            retry_status_code=retry_response.status_code,
                            status="failed"
                        ).errorf("[REQ-%s] Retry request failed: %d", request_id, retry_response.status_code)
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
            chat_log.with_fields(
                event_type="non_stream_error",
                request_id=request_id,
                error=str(e)
            ).errorf("[REQ-%s] Error in non-streaming request: %s", request_id, e)
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