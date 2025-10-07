# Embeddings routes
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any, Union, Optional
import asyncio
import time
from datetime import datetime, timezone

from ....schemas import openai as openai_schemas
from ....crud import session as session_crud
from ....crud import api_key as api_key_crud
from ....core.config import settings
from ....core.model_routing import async_model_router
from ....services import session_service
from ....services import proxy_router_service
from ....db.database import get_db
from ....dependencies import get_api_key_user
from ....db.models import User
from ....core.logging_config import get_api_logger

router = APIRouter(tags=["Embeddings"])


logger = get_api_logger()

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
    session_logger = logger.bind(endpoint="automated_session_creation", user_id=user.id, model=requested_model)
    session_logger.info("Creating automated session for embeddings model",
                       requested_model=requested_model,
                       api_key_id=db_api_key.id,
                       event_type="session_creation_start")
    
    try:
        # Create automated session
        new_session = await session_service.create_automated_session(
            db=db,
            api_key_id=db_api_key.id,
            user_id=user.id,
            requested_model=requested_model
        )
        
        session_id = new_session.id
        session_logger.info("Created new embeddings session",
                           session_id=session_id,
                           event_type="session_creation_success")
        
        # Add a small delay to ensure the session is fully registered
        session_logger.debug("Adding brief delay to ensure session is fully registered")
        await asyncio.sleep(1.0)  # 1 second delay
        
        return session_id
        
    except Exception as e:
        session_logger.error("Error creating automated embeddings session",
                           error=str(e),
                           requested_model=requested_model,
                           event_type="session_creation_error",
                           exc_info=True)
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
    embeddings_logger = logger.bind(endpoint="create_embeddings", 
                                   user_id=user.id, 
                                   model=request_data.model)
    embeddings_logger.info("Embeddings request received",
                          model=request_data.model,
                          input_type=type(request_data.input).__name__,
                          encoding_format=request_data.encoding_format,
                          dimensions=request_data.dimensions,
                          event_type="embeddings_request_start")
    
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
        embeddings_logger.info("Resolving model to blockchain ID",
                              model_name=request_data.model,
                              event_type="model_resolution_start")
        try:
            target_model_id = await async_model_router.get_target_model(request_data.model)
            embeddings_logger.info("Model resolved successfully",
                                  model_name=request_data.model,
                                  blockchain_id=target_model_id,
                                  event_type="model_resolution_success")
        except Exception as e:
            embeddings_logger.error("Error resolving model",
                                   model_name=request_data.model,
                                   error=str(e),
                                   event_type="model_resolution_error")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid model '{request_data.model}': {e}"
            )
        
        # Check for existing active session
        session = await session_crud.get_active_session_by_api_key(db, db_api_key.id)
        session_id = None
        
        if session and not session.is_expired:
            # Check if the session model matches the requested model
            embeddings_logger.info("Found existing session",
                                  session_id=session.id,
                                  session_model=session.model,
                                  event_type="existing_session_found")
            
            if session.model == target_model_id:
                session_id = session.id
                embeddings_logger.info("Using existing session - model match",
                                      session_id=session_id,
                                      event_type="session_reuse")
            else:
                embeddings_logger.info("Session model mismatch - creating new session",
                                      session_model=session.model,
                                      requested_model=target_model_id,
                                      event_type="session_model_mismatch")
                # Create new session for different model
                session_id = await _handle_automated_session_creation(db, user, db_api_key, request_data.model)
        else:
            if session:
                embeddings_logger.info("Session is expired - creating new session",
                                      expired_session_id=session.id,
                                      event_type="session_expired")
            else:
                embeddings_logger.info("No existing session found - creating new session",
                                      event_type="no_session_found")
            
            # Create new session
            session_id = await _handle_automated_session_creation(db, user, db_api_key, request_data.model)
        
        # Forward request to proxy-router using SDK
        embeddings_logger.info("Forwarding embeddings request to proxy-router",
                               session_id=session_id,
                               model_id=target_model_id,
                               event_type="proxy_request_start")
        
        try:
            response = await proxy_router_service.embeddings(
                session_id=session_id,
                input_data=request_data.input,
                model=target_model_id,  # Use blockchain ID
                encoding_format=request_data.encoding_format,
                dimensions=request_data.dimensions,
                user=request_data.user
            )
            
            embeddings_logger.info("Proxy-router response received",
                                   status_code=response.status_code,
                                   event_type="proxy_response_received")
            
            if response.status_code == 200:
                response_data = response.json()
                
                # Count embeddings in response for metrics
                embedding_count = len(response_data.get("data", [])) if "data" in response_data else 0
                
                # Ensure the response model field shows the original model name, not blockchain ID
                if "model" in response_data:
                    response_data["model"] = request_data.model
                
                embeddings_logger.info("Successfully processed embeddings request",
                                      embedding_count=embedding_count,
                                      session_id=session_id,
                                      event_type="embeddings_success")
                return JSONResponse(content=response_data)
            else:
                embeddings_logger.error("Proxy-router error",
                                       status_code=response.status_code,
                                       error_response=response.text,
                                       session_id=session_id,
                                       event_type="proxy_error")
                
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
        
        except proxy_router_service.ProxyRouterServiceError as e:
            embeddings_logger.error("Proxy router service error",
                                   error=str(e),
                                   status_code=e.status_code,
                                   error_type=e.error_type,
                                   session_id=session_id,
                                   event_type="proxy_service_error")
            raise HTTPException(
                status_code=e.get_http_status_code(),
                detail=f"Embeddings request failed: {e.message}"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        embeddings_logger.error("Unexpected error in embeddings endpoint",
                               error=str(e),
                               model=request_data.model,
                               event_type="embeddings_unexpected_error",
                               exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )
