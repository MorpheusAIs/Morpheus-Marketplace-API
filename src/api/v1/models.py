# Placeholder for OpenAI models routes 

from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List
import time

from ...dependencies import get_api_key_user
from ...db.models import User
from ...schemas import openai as openai_schemas
from ...services.model_mapper import model_mapper

router = APIRouter(tags=["models"])


@router.get("", response_model=openai_schemas.ModelList)
async def list_models(
    refresh_cache: bool = Query(False, description="Force refresh the models cache"),
    current_user: User = Depends(get_api_key_user)
):
    """
    Get a list of available models.
    
    Uses Redis caching for efficient responses. The cache can be
    bypassed by setting refresh_cache=True.
    """
    # Fetch models using the model_mapper service, which handles caching
    models = await model_mapper.get_all_models(force_refresh=refresh_cache)
    
    return openai_schemas.ModelList(data=models)


@router.get("/{model_id}", response_model=openai_schemas.Model)
async def get_model(
    model_id: str,
    refresh_cache: bool = Query(False, description="Force refresh the model cache"),
    current_user: User = Depends(get_api_key_user)
):
    """
    Get information about a specific model.
    
    Uses Redis caching for efficient responses. The cache can be
    bypassed by setting refresh_cache=True.
    """
    # Fetch the specific model using the model_mapper service
    model = await model_mapper.get_model_by_id(model_id, force_refresh=refresh_cache)
    
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model '{model_id}' not found"
        )
    
    return model 