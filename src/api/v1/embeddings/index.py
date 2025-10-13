# Embeddings routes
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any, Union, Optional
import asyncio
import time
import uuid
from datetime import datetime, timezone

from ....schemas import openai as openai_schemas
from ....crud import session as session_crud
from ....crud import api_key as api_key_crud
from ....core.config import settings
from ....core.model_routing import async_model_router
from ....services import session_service
from ....services import proxy_router_service
from ....db.database import get_db
from ....dependencies import get_api_key_user, get_current_api_key
from ....db.models import User, APIKey
from ....core.logging_config import get_api_logger

router = APIRouter(tags=["Embeddings"])

logger = get_api_logger()

from .models import EmbeddingRequest, EmbeddingResponse

@router.post("/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(
    request_data: EmbeddingRequest,
    request: Request,
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db),
    db_api_key: APIKey = Depends(get_current_api_key),
):
    """
    Create embeddings for the given input text(s).
    
    This endpoint creates embeddings using the Morpheus Network providers.
    It automatically manages sessions and routes requests to the appropriate embedding model.
    """
    request_id = str(uuid.uuid4())[:8]  # Generate short request ID for tracing
    embeddings_logger = logger.bind(endpoint="create_embeddings", 
                                   user_id=user.id, 
                                   model=request_data.model,
                                   request_id=request_id)
    embeddings_logger.info("Embeddings request received",
                          model=request_data.model,
                          input_type=type(request_data.input).__name__,
                          encoding_format=request_data.encoding_format,
                          dimensions=request_data.dimensions,
                          event_type="embeddings_request_start")
    
    try:        
        session_id = request_data.session_id
        requested_model = request_data.model

        if not session_id:
            try:
                embeddings_logger.info("No session_id in request, attempting to retrieve or create one",
                           request_id=request_id,
                           api_key_id=db_api_key.id,
                           requested_model=requested_model,
                           event_type="session_lookup_start")
                session = await session_service.get_session_for_api_key(db, db_api_key.id, user.id, requested_model, model_type='EMBEDDINGS')
                if session:
                    session_id = session.id
                    embeddings_logger.info("Session retrieved successfully",
                                request_id=request_id,
                                session_id=session_id,
                                event_type="session_lookup_success")
            except Exception as e:
                embeddings_logger.error("Error in session handling",
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
            embeddings_logger.error("No session ID after all attempts",
                            request_id=request_id,
                            api_key_id=db_api_key.id,
                            requested_model=requested_model,
                            event_type="no_session_available")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No session ID provided in request and no active session found for API key"
            )
        
        try:
            response = await proxy_router_service.embeddings(
                session_id=session_id,
                input_data=request_data.input,
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
