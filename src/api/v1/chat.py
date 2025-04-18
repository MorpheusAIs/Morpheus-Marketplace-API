# Chat routes 

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any
import json
import httpx
import logging
from datetime import datetime
import uuid
import asyncio
import base64

from ...dependencies import get_api_key_user, api_key_header
from ...db.database import get_db
from ...db.models import User
from ...schemas import openai as openai_schemas
from ...crud import session as session_crud
from ...crud import api_key as api_key_crud
from ...core.config import settings

router = APIRouter(tags=["Chat"])

# Authentication credentials for proxy-router
AUTH = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)


@router.post("/completions", response_model=None)
async def create_chat_completion(
    request: Request,
    api_key: str = Depends(api_key_header),
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a chat completion.
    
    This implementation connects to the proxy-router to interact with the selected model.
    The session is automatically retrieved from the database based on the API key,
    or can be explicitly provided in the request body via session_id parameter.
    """
    # Check if we have a valid user from the API key
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get the raw request body
    body = await request.body()
    
    # Extract session_id from body if present
    session_id = None
    try:
        json_body = json.loads(body)
        session_id = json_body.get("session_id")
        
        # If session_id is in body, remove it and recreate the body
        if session_id:
            del json_body["session_id"]
            body = json.dumps(json_body).encode('utf-8')
    except Exception as e:
        logging.error(f"Error processing request body: {e}")
    
    # If no session_id from body, try to get from database
    if not session_id and user.api_keys:
        try:
            api_key_prefix = user.api_keys[0].key_prefix
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
                    detail="No active session found for API key. Please create a session first using /session/modelsession endpoint."
                )
            
            # Use the session ID from the database
            session_id = session.session_id
            logging.info(f"Using session ID from database: {session_id}")
        except HTTPException:
            # Re-raise HTTP exceptions
            raise
        except Exception as e:
            logging.error(f"Error looking up session: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error retrieving session: {str(e)}"
            )
    
    # If we still don't have a session_id, return an error
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No session ID provided in request and no active session found for API key"
        )
    
    # Forward request to proxy-router
    endpoint = f"{settings.PROXY_ROUTER_URL}/v1/chat/completions"
    
    # Create basic auth header - this is critical
    auth_str = f"{settings.PROXY_ROUTER_USERNAME}:{settings.PROXY_ROUTER_PASSWORD}"
    auth_b64 = base64.b64encode(auth_str.encode('ascii')).decode('ascii')
    
    # Simple headers that work with the proxy router
    headers = {
        "authorization": f"Basic {auth_b64}",
        "Content-Type": "application/json",
        "accept": "text/event-stream",
        "session_id": session_id
    }
    
    logging.info(f"Making request to {endpoint} with session_id: {session_id}")
    logging.info(f"Headers: {headers}")
    
    # Handle streaming only - assume all requests are streaming
    async def stream_generator():
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    endpoint,
                    content=body,
                    headers=headers,
                    timeout=60.0
                ) as response:
                    # Log response info
                    logging.info(f"Proxy router response status: {response.status_code}")
                    logging.info(f"Proxy router response headers: {response.headers}")
                    if response.status_code != 200:
                        error_msg = f"Proxy router returned non-200 status: {response.status_code}"
                        logging.error(error_msg)
                        try:
                            error_content = await response.aread()
                            logging.error(f"Error content: {error_content.decode('utf-8')}")
                        except:
                            pass
                        
                        yield f"data: {{\"error\": {{\"message\": \"{error_msg}\", \"type\": \"StreamingError\"}}}}\n\n"
                        return
                    
                    # Simply forward the stream response with minimal processing
                    async for chunk in response.aiter_bytes():
                        yield chunk.decode('utf-8')
        
        except Exception as e:
            logging.error(f"Error in streaming: {e}")
            yield f"data: {{\"error\": {{\"message\": \"Error in streaming: {str(e)}\", \"type\": \"StreamingError\"}}}}\n\n"
    
    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    ) 