# Placeholder for OpenAI models routes 

from fastapi import APIRouter, HTTPException, status, Query, Depends, Path
from typing import List, Dict, Any
import time
import httpx
import uuid
import json

from ...schemas import openai as openai_schemas
from ...services.model_mapper import model_mapper
from ...core.config import settings

router = APIRouter(tags=["models"])

# Authentication credentials
AUTH = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)

@router.get("", response_model=None)
async def list_models(
    refresh_cache: bool = Query(False, description="Force refresh the models cache"),
    include_raw: bool = Query(False, description="Include raw blockchain data in response"),
    format: str = Query("openai", description="Format of response: 'openai' or 'blockchain'")
):
    """
    Get a list of available models.
    
    Uses Redis caching for efficient responses. The cache can be
    bypassed by setting refresh_cache=True.
    Set include_raw=true to see the raw blockchain model data.
    Set format=blockchain to get the raw blockchain model format.
    """
    try:
        # Direct fetch from blockchain API
        blockchain_endpoint = f"{settings.PROXY_ROUTER_URL}/blockchain/models"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                blockchain_endpoint,
                auth=AUTH,
                timeout=10.0
            )
            response.raise_for_status()
            
            blockchain_data = response.json()
            blockchain_models = blockchain_data.get("models", [])
            
            # For blockchain format, return raw data
            if format.lower() == "blockchain":
                return {
                    "count": len(blockchain_models),
                    "models": blockchain_models
                }
            
            # For debugging, return raw data when requested
            if include_raw:
                return {
                    "object": "list",
                    "data": blockchain_models
                }
            
            # Convert blockchain models to OpenAI format
            models = []
            for model in blockchain_models:
                model_name = model.get("Name", "unknown-model")
                created_timestamp = model.get("CreatedAt", int(time.time()))
                blockchain_id = model.get("Id", "")
                owner = model.get("Owner", "morpheus")
                
                # Create a unique permission ID
                permission_id = f"modelperm-{uuid.uuid4().hex[:8]}"
                
                # Get model tags
                tags = model.get("Tags", [])
                
                # Include the blockchain ID in the name for easy reference
                model_name_with_id = f"{model_name} [ID:{blockchain_id}]"
                
                # Create OpenAI-compatible model with blockchain ID included
                openai_model = {
                    "id": model_name,
                    "object": "model", 
                    "created": created_timestamp,
                    "owned_by": owner,
                    "root": model_name,
                    "parent": None,
                    "blockchain_id": blockchain_id,  # Include blockchain ID
                    "tags": tags,                   # Include tags
                    "permission": [
                        {
                            "id": permission_id,
                            "object": "model_permission",
                            "created": created_timestamp,
                            "allow_create_engine": False,
                            "allow_sampling": True,
                            "allow_logprobs": True,
                            "allow_search_indices": False,
                            "allow_view": True,
                            "allow_fine_tuning": False,
                            "organization": "morpheus",
                            "group": None,
                            "is_blocking": False
                        }
                    ]
                }
                
                models.append(openai_model)
            
            return {"object": "list", "data": models}
    except httpx.HTTPStatusError as e:
        # Handle HTTP errors and return detailed error messages
        import logging
        logging.error(f"HTTP error getting models: {e}")
        try:
            error_detail = e.response.json()
            if isinstance(error_detail, dict):
                if "error" in error_detail:
                    detail_message = error_detail["error"]
                elif "detail" in error_detail:
                    detail_message = error_detail["detail"]
                else:
                    detail_message = json.dumps(error_detail)
            else:
                detail_message = str(error_detail)
        except:
            detail_message = f"Status code: {e.response.status_code}, Reason: {e.response.reason_phrase}"
            
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Error fetching models from blockchain: {detail_message}"
        )
    except Exception as e:
        # Handle other errors
        import logging
        logging.error(f"Error getting models: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching models: {str(e)}"
        )


@router.get("/bids/rated")
async def get_rated_bids(
    model_id: str = Query(..., description="The blockchain ID (hex) of the model to get rated bids for, e.g. 0x1234...")
):
    """
    Get rated bids for a specific model.
    
    Connects to the proxy-router's /blockchain/models/{id}/bids/rated endpoint.
    Note: Use the blockchain model ID (hex) not the name.
    """
    try:
        # Connect to proxy-router
        endpoint = f"{settings.PROXY_ROUTER_URL}/blockchain/models/{model_id}/bids/rated"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                endpoint,
                auth=AUTH,
                timeout=10.0
            )
            response.raise_for_status()
            
            return response.json()
    except httpx.HTTPStatusError as e:
        # Handle HTTP errors with detailed information
        import logging
        logging.error(f"HTTP error getting rated bids: {e}")
        try:
            error_detail = e.response.json()
            if isinstance(error_detail, dict):
                if "error" in error_detail:
                    detail_message = error_detail["error"]
                elif "detail" in error_detail:
                    detail_message = error_detail["detail"]
                else:
                    detail_message = json.dumps(error_detail)
            else:
                detail_message = str(error_detail)
        except:
            detail_message = f"Status code: {e.response.status_code}, Reason: {e.response.reason_phrase}"
            
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Error fetching rated bids: {detail_message}"
        )
    except Exception as e:
        # Handle other errors
        import logging
        logging.error(f"Error getting rated bids: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching rated bids: {str(e)}"
        ) 