# Embeddings routes
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any, Union, Optional
import httpx
import json
import asyncio
import time
from datetime import datetime, timezone

from ...schemas import openai as openai_schemas
from ...crud import session as session_crud
from ...crud import api_key as api_key_crud
from ...core.config import settings
from ...core.model_routing import async_model_router
from ...services import session_service
from ...db.database import get_db
from ...dependencies import get_api_key_user
from ...core.structured_logger import API_LOG

# Setup structured logging (API endpoints category)
embeddings_log = API_LOG.named("EMBEDDINGS")
from ...db.models import User

router = APIRouter(tags=["Embeddings"])

# Authentication credentials for proxy-router
AUTH = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)


class EmbeddingRequest(openai_schemas.BaseModel):
    """Request model for embeddings endpoint"""
    input: Union[str, List[str]]
    model: str
    encoding_format: Optional[str] = "float"
    dimensions: Optional[int] = None
    user: Optional[str] = None

class EmbeddingObject(openai_schemas.BaseModel):
    """Individual embedding object"""
    object: str = "embedding"
    embedding: List[float]
    index: int

class EmbeddingUsage(openai_schemas.BaseModel):
    """Usage statistics for embedding request"""
    prompt_tokens: int
    total_tokens: int

class EmbeddingResponse(openai_schemas.BaseModel):
    """Response model for embeddings endpoint"""
    object: str = "list"
    data: List[EmbeddingObject]
    model: str
    usage: EmbeddingUsage

async def _handle_automated_session_creation(db: AsyncSession, user: User, db_api_key, requested_model: str) -> str:
    """Handle automated session creation for embeddings"""
    embeddings_log.with_fields(
        event_type="session_creation",
        requested_model=requested_model,
        user_id=user.id,
        api_key_id=api_key.id
    ).infof("Creating automated session for embeddings model: %s", requested_model)
    
    try:
        # Create automated session
        new_session = await session_service.create_automated_session(
            db=db,
            api_key_id=db_api_key.id,
            user_id=user.id,
            requested_model=requested_model
        )
        
        session_id = new_session.id
        embeddings_log.with_fields(
            event_type="session_creation",
            session_id=session_id,
            status="success"
        ).infof("Created new embeddings session: %s", session_id)
        
        # Add a small delay to ensure the session is fully registered
        embeddings_log.with_fields(
            event_type="session_delay",
            session_id=session_id
        ).info("Adding a brief delay to ensure session is fully registered")
        await asyncio.sleep(1.0)  # 1 second delay
        
        return session_id
        
    except Exception as e:
        embeddings_log.with_fields(
            event_type="session_creation",
            status="failed",
            error=str(e)
        ).errorf("Error creating automated embeddings session: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to create embeddings session: {e}"
        )

@router.post("/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(
    request_data: EmbeddingRequest,
    request: Request,
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create embeddings for the given input text(s).
    
    This endpoint creates embeddings using the Morpheus Network providers.
    It automatically manages sessions and routes requests to the appropriate embedding model.
    """
    embeddings_log.with_fields(
        event_type="embeddings_request",
        requested_model=request_data.model,
        user_id=user.id,
        input_count=len(request_data.input) if isinstance(request_data.input, list) else 1
    ).infof("Embeddings request received for model: %s", request_data.model)
    
    try:
        # Get API key from user (same pattern as chat endpoint)
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
        
        # Resolve the model to blockchain ID
        embeddings_log.with_fields(
            event_type="model_resolution",
            requested_model=request_data.model
        ).infof("Resolving model '%s' to blockchain ID", request_data.model)
        try:
            target_model_id = await async_model_router.get_target_model(request_data.model)
            embeddings_log.with_fields(
                event_type="model_resolution",
                requested_model=request_data.model,
                resolved_id=target_model_id,
                status="success"
            ).infof("Resolved model '%s' to ID: %s", request_data.model, target_model_id)
        except Exception as e:
            embeddings_log.with_fields(
                event_type="model_resolution",
                requested_model=request_data.model,
                status="failed",
                error=str(e)
            ).errorf("Error resolving model '%s': %s", request_data.model, e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid model '{request_data.model}': {e}"
            )
        
        # Check for existing active session
        session = await session_crud.get_active_session_by_api_key(db, db_api_key.id)
        session_id = None
        
        if session and not session.is_expired:
            # Check if the session model matches the requested model
            embeddings_log.with_fields(
                event_type="session_reuse",
                session_id=session.id,
                session_model=session.model
            ).infof("Found existing session %s with model: %s", session.id, session.model)
            
            if session.model == target_model_id:
                session_id = session.id
                embeddings_log.with_fields(
                    event_type="session_reuse",
                    session_id=session_id,
                    status="model_match"
                ).infof("Using existing session %s (model match)", session_id)
            else:
                embeddings_log.with_fields(
                    event_type="session_mismatch",
                    session_id=session.id,
                    session_model=session.model,
                    requested_model=target_model_id
                ).infof("Session model mismatch. Session: %s, Requested: %s", session.model, target_model_id)
                # Create new session for different model
                session_id = await _handle_automated_session_creation(db, user, db_api_key, request_data.model)
        else:
            if session:
                embeddings_log.with_fields(
                    event_type="session_expired",
                    session_id=session.id
                ).infof("Session %s is expired, creating new session", session.id)
            else:
                embeddings_log.with_fields(
                    event_type="session_creation_needed",
                    reason="no_existing_session"
                ).info("No existing session found, creating new session")
            
            # Create new session
            session_id = await _handle_automated_session_creation(db, user, db_api_key, request_data.model)
        
        # Prepare request for proxy-router
        proxy_request_data = {
            "input": request_data.input,
            "model": target_model_id,  # Use blockchain ID
            "encoding_format": request_data.encoding_format,
        }
        
        if request_data.dimensions:
            proxy_request_data["dimensions"] = request_data.dimensions
        if request_data.user:
            proxy_request_data["user"] = request_data.user
        
        # Forward request to proxy-router embeddings endpoint
        endpoint = f"{settings.PROXY_ROUTER_URL}/v1/embeddings"
        
        # Add session_id as query parameter
        params = {"session_id": session_id}
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        embeddings_log.with_fields(
            event_type="proxy_request",
            endpoint=endpoint,
            session_id=session_id,
            model_id=target_model_id
        ).infof("Forwarding embeddings request to proxy-router: %s", endpoint)
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                endpoint,
                json=proxy_request_data,
                headers=headers,
                params=params,
                auth=AUTH,
                timeout=60.0
            )
            
            embeddings_log.with_fields(
                event_type="proxy_response",
                status_code=response.status_code,
                session_id=session_id
            ).infof("Proxy-router response status: %d", response.status_code)
            
            if response.status_code == 200:
                response_data = response.json()
                
                # Ensure the response model field shows the original model name, not blockchain ID
                if "model" in response_data:
                    response_data["model"] = request_data.model
                
                embeddings_log.with_fields(
                    event_type="embeddings_success",
                    session_id=session_id,
                    model_id=target_model_id
                ).info("Successfully processed embeddings request")
                return JSONResponse(content=response_data)
            else:
                embeddings_log.with_fields(
                    event_type="proxy_error",
                    status_code=response.status_code,
                    session_id=session_id,
                    error_text=response.text[:200]  # Limit error text length
                ).errorf("Proxy-router error: %d - %s", response.status_code, response.text)
                
                # Try to parse error response
                try:
                    error_data = response.json()
                    error_message = error_data.get("detail", error_data.get("error", response.text))
                except:
                    error_message = response.text
                
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Embeddings request failed: {error_message}"
                )
    
    except HTTPException:
        raise
    except Exception as e:
        embeddings_log.with_fields(
            event_type="embeddings_error",
            error=str(e),
            session_id=session_id if 'session_id' in locals() else None,
            model=request_data.model if 'request_data' in locals() else None
        ).errorf("Unexpected error in embeddings endpoint: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )
