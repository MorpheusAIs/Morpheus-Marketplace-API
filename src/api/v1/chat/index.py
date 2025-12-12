# Chat routes 
"""
This module handles chat completion endpoints for the API gateway.

Key behaviors:
- Respects client's 'stream' parameter in requests (true/false)
- Returns streaming responses only when requested (stream=true)
- Returns regular JSON responses when streaming is not requested (stream=false)
- Warning: Tool calling may require streaming with some models
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse, JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any, List, Union
import json
import uuid
import asyncio

from ....dependencies import get_api_key_user, get_current_api_key
from ....db.database import get_db
from ....db.models import User, APIKey
from ....crud import session as session_crud
from ....crud import api_key as api_key_crud
from ....core.config import settings
from ....core.model_routing import model_router
from ....services import session_service
from ....services.session_service import AutomationDisabledException
from ....core.logging_config import get_api_logger
from ....services.proxy_router_service import (
    healthcheck,
    getModels,
    ProxyRouterServiceError,
)
from .chat_models import (
    ChatCompletionRequest,
    ChatMessage,
    Tool,
    ToolChoice,
    ToolFunction,
)
from .chat_utils import (
    fix_tool_choice_structure,
    remove_tool_choice_from_tools,
    normalize_assistant_tool_call_messages,
    log_tool_request_details,
)
from .chat_streaming import build_stream_generator
from .chat_non_streaming import handle_non_streaming_request

router = APIRouter(tags=["Chat"])

"""
Re-exported models for backwards compatibility:
 - ChatMessage, ToolFunction, Tool, ToolChoice, ChatCompletionRequest
"""

@router.post("/completions")
async def create_chat_completion(
    request_data: ChatCompletionRequest,
    request: Request,
    user: User = Depends(get_api_key_user),
    db_api_key: APIKey = Depends(get_current_api_key)
):
    """
    Create a chat completion with automatic session creation if enabled.
    
    Supports both streaming and non-streaming responses based on the 'stream' parameter.
    Tool calling is supported but may work better with streaming enabled.
    
    Note: This endpoint uses short-lived DB connections for auth and session lookup
    (released immediately) to avoid exhausting the connection pool during long-running
    streaming requests.
    """
    # Auth connections already released by the dependency functions
    
    logger = get_api_logger()
    request_id = str(uuid.uuid4())[:8]  # Generate short request ID for tracing
    
    chat_logger = logger.bind(endpoint="create_chat_completion", 
                             user_id=user.id,
                             request_id=request_id)
    
    chat_logger.info("New chat completion request received",
                    request_id=request_id,
                    model=request_data.model,
                    stream_requested=request_data.stream,
                    event_type="chat_completion_request_start")
    
    # Check if we have a valid user from the API key
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Extract request details for logging
    chat_logger.info("Processing chat completion request for user",
                    user_id=user.id,
                    api_key_id=db_api_key.id,
                    event_type="chat_request_processing")
    
    json_body = request_data.model_dump(exclude_none=True)
    has_tools = "tools" in json_body and json_body["tools"]

    # Use the client's stream parameter directly
    # If stream is None (not specified), default to False for consistency
    should_stream = request_data.stream if request_data.stream is not None else False
    
    # Check for tool requests with streaming disabled
    if has_tools and not should_stream:
        chat_logger.warning("Tool calling requested with stream=false - this may cause issues with some models",
                           request_id=request_id,
                           has_tools=has_tools,
                           stream_enabled=should_stream,
                           event_type="tool_calling_stream_warning")
    
    json_body["stream"] = should_stream
    
    chat_logger.info("Chat request configuration",
                     request_id=request_id,
                     client_stream_requested=should_stream,
                     has_tools=has_tools,
                     proxy_stream_config=json_body['stream'],
                     event_type="chat_request_config")

    # Extract session_id and model from request
    session_id = json_body.pop("session_id", None)
    requested_model = json_body.pop("model", None)
    
    # Log tool-related parameters if present (for debugging)
    if "tools" in json_body:
        tool_count = len(json_body['tools']) if isinstance(json_body['tools'], list) else 0
        chat_logger.info("Request includes tools",
                        request_id=request_id,
                        tool_count=tool_count,
                        event_type="tools_detected")
    if "tool_choice" in json_body:
        chat_logger.info("Request includes tool_choice",
                        request_id=request_id,
                        tool_choice=json_body['tool_choice'],
                        event_type="tool_choice_detected")
    
    body = json.dumps(json_body).encode('utf-8')
    
    # Log the original request details
    chat_logger.info("Original request details",
                    request_id=request_id,
                    session_id=session_id,
                    requested_model=requested_model,
                    event_type="request_details")
    
    # If no session_id from body, try to get from database using API key header (avoid lazy-loading relationships)
    if not session_id:
        try:
            chat_logger.info("No session_id in request, attempting to retrieve or create one",
                           request_id=request_id,
                           api_key_id=db_api_key.id,
                           requested_model=requested_model,
                           event_type="session_lookup_start")
            async with get_db() as db:
                session = await session_service.get_session_for_api_key(db, db_api_key.id, user.id, requested_model, model_type='LLM')
                if session:
                    session_id = session.id
                    chat_logger.info("Session retrieved successfully",
                                   request_id=request_id,
                                   session_id=session_id,
                                   event_type="session_lookup_success")
        except AutomationDisabledException as e:
            chat_logger.warning("Session automation is disabled for user",
                              request_id=request_id,
                              user_id=e.user_id,
                              event_type="automation_disabled_error")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Session automation is disabled. Please enable it in your Account settings to use Chat."
            )
        except Exception as e:
            chat_logger.error("Error in session handling",
                            request_id=request_id,
                            error=str(e),
                            event_type="session_handling_error",
                            exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error handling session: {str(e)}"
            )
    
    # If we still don't have a session_id, return an error
    if not session_id:
        chat_logger.error("No session ID after all attempts",
                         request_id=request_id,
                         api_key_id=db_api_key.id,
                         requested_model=requested_model,
                         event_type="no_session_available")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No session ID provided in request and no active session found for API key"
        )
    
    # Apply request fixes for tool calling compatibility
    fix_tool_choice_structure(json_body, chat_logger)

    # Additional fix for tool_choice within tools parameters
    remove_tool_choice_from_tools(json_body, chat_logger)

    # Special handling for message with tool_calls and empty content
    normalize_assistant_tool_call_messages(json_body, chat_logger)

    # Log request details for debugging
    log_tool_request_details(json_body, session_id, chat_logger)
    
    # Build streaming generator for response handling
    # Note: stream_generator and handle_non_streaming_request will create their own
    # short-lived DB sessions as needed, so we don't pass a db parameter
    stream_generator = build_stream_generator(
        logger=chat_logger,
        session_id=session_id,
        body=body,
        requested_model=requested_model,
        db_api_key=db_api_key,
        user=user,
    )
    
    # Handle request based on streaming preference
    if should_stream:
        chat_logger.info("Returning streaming response",
                        request_id=request_id,
                        session_id=session_id,
                        event_type="streaming_response_start")
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
        chat_logger.info("Processing non-streaming request",
                        request_id=request_id,
                        session_id=session_id,
                        event_type="non_streaming_request_start")
        try:
            return await handle_non_streaming_request(
                logger=chat_logger,
                request_id=request_id,
                body=body,
                db_api_key=db_api_key,
                user=user,
                requested_model=requested_model,
                session_id=session_id,
            )
        except Exception as e:
            chat_logger.error("Error in non-streaming request",
                            request_id=request_id,
                            error=str(e),
                            session_id=session_id,
                            event_type="non_streaming_request_error",
                            exc_info=True)
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