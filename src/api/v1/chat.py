# Placeholder for OpenAI chat completion routes 

from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPBearer, APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List, Dict, Any
import json
import time
import uuid
import asyncio
import httpx

from ...dependencies import get_api_key_user, api_key_header
from ...db.database import get_db
from ...db.models import User
from ...schemas import openai as openai_schemas
from ...crud import private_key as private_key_crud
from ...services.model_mapper import model_mapper
from ...core.config import settings

router = APIRouter(tags=["chat"])

# Authentication credentials for proxy-router
AUTH = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)


@router.post("/completions", response_model=None)
async def create_chat_completion(
    request: Request,
    body: openai_schemas.ChatCompletionRequest,
    api_key: Optional[str] = Depends(api_key_header),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a chat completion.
    
    This implementation connects to the proxy-router to interact with the selected model.
    The session_id header MUST be provided and should be the blockchain session ID (hex) 
    obtained when creating a session with blockchain/sessions/model or blockchain/sessions/bid.
    """
    # Get session_id from headers
    session_id = request.headers.get("session_id")
    
    # Log information for debugging
    print(f"Headers: {request.headers}")
    print(f"Session ID: {session_id}")
    
    # Verify API key if provided
    user = None
    if api_key:
        # Extract the key without the Bearer prefix
        if api_key.startswith("Bearer "):
            api_key = api_key.replace("Bearer ", "")
            
        # Simple validation for demo purposes - in production, use proper auth
        if not api_key.startswith("sk-"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key format. Must start with sk-",
                headers={"WWW-Authenticate": "Bearer"},
            )
    
    # Check if session_id is provided
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A session_id header is required. Please create a session first using blockchain/sessions/model or blockchain/sessions/bid endpoints."
        )
    
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
                print(f"Proxy router response status: {response.status_code}")
                
                # Handle the case where the response starts with 'data:'
                if response_text.startswith('data:'):
                    # Strip the 'data:' prefix and any whitespace
                    json_text = response_text.replace('data:', '').strip()
                    # Try to parse the JSON
                    try:
                        parsed_json = json.loads(json_text)
                        return JSONResponse(content=parsed_json)
                    except Exception as json_err:
                        print(f"JSON parsing error: {json_err}")
                        # Return raw text as fallback
                        return PlainTextResponse(content=response_text.strip())
                
                # Normal JSON response
                try:
                    return JSONResponse(content=response.json())
                except Exception as e:
                    print(f"JSON parsing error: {e}")
                    # Return text response as fallback
                    return PlainTextResponse(content=response_text.strip())
    
    except httpx.HTTPStatusError as e:
        # Handle HTTP errors from the proxy-router
        import logging
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
        import logging
        logging.error(f"Error in chat completion: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error in chat completion: {str(e)}"
        ) 