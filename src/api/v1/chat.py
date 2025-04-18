# Chat routes 

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any
import json
import httpx
import logging

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
    body: openai_schemas.ChatCompletionRequest,
    api_key: str = Depends(api_key_header),
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a chat completion.
    
    This implementation connects to the proxy-router to interact with the selected model.
    The session is automatically retrieved from the database based on the API key.
    """
    # Check if we have a valid user from the API key
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get API key
    if not user.api_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No API keys found for user"
        )
    
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
    
    # Forward request to proxy-router
    endpoint = f"{settings.PROXY_ROUTER_URL}/v1/chat/completions"
    
    headers = {"session_id": session_id}
    
    # Handle streaming or non-streaming requests by forwarding to the proxy-router
    try:
        async with httpx.AsyncClient() as client:
            if body.stream:
                # For streaming, we'll create a StreamingResponse that forwards the proxy-router's streaming response
                async def stream_generator():
                    async with client.stream(
                        "POST",
                        endpoint,
                        json=body.dict(),
                        headers=headers,
                        auth=AUTH,
                        timeout=60.0
                    ) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_text():
                            yield chunk
                
                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream"
                )
            else:
                # For non-streaming, just forward the request and return the response
                response = await client.post(
                    endpoint,
                    json=body.dict(),
                    headers=headers,
                    auth=AUTH,
                    timeout=60.0
                )
                response.raise_for_status()
                
                # Get the raw response text
                response_text = response.text
                logging.info(f"Proxy router response status: {response.status_code}")
                
                # Handle the case where the response starts with 'data:'
                if response_text.startswith('data:'):
                    # Strip the 'data:' prefix and any whitespace
                    json_text = response_text.replace('data:', '').strip()
                    # Try to parse the JSON
                    try:
                        parsed_json = json.loads(json_text)
                        return JSONResponse(content=parsed_json)
                    except Exception as json_err:
                        logging.error(f"JSON parsing error: {json_err}")
                        # Return raw text as fallback
                        return PlainTextResponse(content=response_text.strip())
                
                # Normal JSON response
                try:
                    return JSONResponse(content=response.json())
                except Exception as e:
                    logging.error(f"JSON parsing error: {e}")
                    # Return text response as fallback
                    return PlainTextResponse(content=response_text.strip())
    
    except httpx.HTTPStatusError as e:
        # Handle HTTP errors from the proxy-router
        logging.error(f"HTTP error from proxy-router: {e}")
        
        # Get error details if available
        error_detail = "Unknown error"
        try:
            error_response = e.response.json()
            if "error" in error_response:
                error_detail = error_response["error"]
            elif "detail" in error_response:
                error_detail = error_response["detail"]
            else:
                error_detail = str(error_response)
        except:
            error_detail = f"Error status code: {e.response.status_code}"
        
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Error from proxy-router: {error_detail}"
        )
    except Exception as e:
        # Handle other errors
        logging.error(f"Error in chat completion: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error in chat completion: {str(e)}"
        ) 