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
            db, user, db_api_key, requested_model, session_duration
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

@router.post("/completions", response_model=None)
async def create_chat_completion(
    request_data: ChatCompletionRequest,
    api_key: str = Depends(api_key_header),
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a chat completion with automatic session creation if enabled.
    """
    # Check if we have a valid user from the API key
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Set up logging for this request
    logger = logging.getLogger(__name__)
    logger.info(f"Processing chat completion request for user {user.id}")
    
    # Convert the request data to a dictionary and then to JSON
    json_body = request_data.model_dump(exclude_none=True)
    session_id = json_body.pop("session_id", None)
    requested_model = json_body.pop("model", None)
    body = json.dumps(json_body).encode('utf-8')
    
    # Log the original request details
    logger.info(f"Original request - session_id: {session_id}, model: {requested_model}")
    
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
                # Use the session ID from the database - using 'id' instead of 'session_id'
                session_id = session.id
                logger.info(f"Using existing session ID from database: {session_id}")
            else:
                logger.info("No active session found, attempting automated session creation")
                # No active session - try automated session creation
                try:
                    automated_session = await session_service.create_automated_session(
                        db,
                        db_api_key.id,
                        user.id,
                        requested_model
                    )
                    
                    if automated_session:
                        # The Session model uses 'id' attribute, not 'session_id'
                        session_id = automated_session.id
                        logger.info(f"Successfully created automated session: {session_id}")
                        
                        # Add a small delay to ensure the session is fully registered
                        logger.info("Adding a brief delay to ensure session is fully registered")
                        await asyncio.sleep(1.0)  # 1 second delay
                    else:
                        # Session creation failed - use mock session for testing
                        mock_session_id = f"mock-{uuid.uuid4()}"
                        logger.warning(f"Automated session creation failed, using mock session ID: {mock_session_id}")
                        session_id = mock_session_id
                except Exception as e:
                    # Error in session creation - use mock session for testing
                    mock_session_id = f"mock-{uuid.uuid4()}"
                    logger.warning(f"Automated session creation error: {str(e)}")
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
    
    # Simple headers that work with the proxy router
    headers = {
        "authorization": f"Basic {auth_b64}",
        "Content-Type": "application/json",
        "accept": "text/event-stream",
        "session_id": session_id,
        "X-Session-ID": session_id  # Try an alternate header format
    }
    
    # Check if we're using a mock session ID
    is_mock_session = session_id.startswith("mock-")
    if is_mock_session:
        logger.info(f"Using mock session ID: {session_id} - will handle response directly")
        
    logger.info(f"Making request to {endpoint} with session_id: {session_id}")
    logger.info(f"Using session_id in header, URL parameter, and X-Session-ID header")
    
    # Handle streaming only - assume all requests are streaming
    async def stream_generator():
        try:
            # For mock sessions, generate a mock response
            if is_mock_session:
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
                yield f"data: {json.dumps(mock_response)}\n\n"
                
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
                yield f"data: {json.dumps(finish_msg)}\n\n"
                yield "data: [DONE]\n\n"
                return
            
            # Try a direct request first - sometimes streaming mode causes issues
            logger.info("Trying direct request to proxy router")
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    endpoint,
                    content=body,
                    headers=headers,
                    timeout=60.0
                )
                
                # Log response info
                logger.info(f"Direct request response status: {response.status_code}")
                
                if response.status_code != 200:
                    error_msg = f"Proxy router returned non-200 status: {response.status_code}"
                    error_type = "ProxyRouterError" # Default type
                    logger.error(error_msg)
                    
                    # Try to get more error details from response
                    try:
                        error_json = response.json()
                        if isinstance(error_json, dict):
                            if "error" in error_json:
                                error_msg = error_json["error"]
                            elif "detail" in error_json:
                                error_msg = error_json["detail"]
                            elif "message" in error_json:
                                error_msg = error_json["message"]
                            
                            if "type" in error_json:
                                error_type = error_json["type"]
                    except:
                        pass
                    
                    error_payload = json.dumps({
                        "error": {
                            "message": error_msg,
                            "type": error_type,
                            "status_code": response.status_code
                        }
                    })
                    
                    yield f"data: {error_payload}\n\n"
                    return
                
                # For successful responses, just yield the response text
                # If it's a streaming response, it will be in the correct format
                # If it's a regular JSON response, we'll wrap it in the SSE format
                response_text = response.text
                
                # Check if this is already SSE format
                if response_text.startswith("data:"):
                    # It's already in SSE format, pass it through
                    yield response_text
                else:
                    # Try to parse as JSON and wrap in SSE format
                    try:
                        result = json.loads(response_text)
                        yield f"data: {json.dumps(result)}\n\n"
                    except:
                        # Just wrap the raw text
                        yield f"data: {response_text}\n\n"
                
        except Exception as e:
            logger.error(f"Error in streaming: {e}")
            logger.exception(e)  # Log full stack trace
            yield f"data: {{\"error\": {{\"message\": \"Error in streaming: {str(e)}\", \"type\": \"StreamingError\"}}}}\n\n"
    
    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    ) 