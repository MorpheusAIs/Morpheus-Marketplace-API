# Model routes 
from fastapi import APIRouter, HTTPException, status, Query, Depends, Path
from typing import List, Dict, Any
import time
import httpx
import uuid
import json

from ...schemas import openai as openai_schemas
from ...services.model_mapper import model_mapper
from ...core.config import settings

router = APIRouter(tags=["Models"])

# Authentication credentials
AUTH = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)

@router.get("/", response_model=None)
async def list_models():
    """
    Get a list of active models.
    
    Response is in OpenAI API format with selected fields from the blockchain data.
    Only returns active models with available providers.
    """
    try:
        # Fetch from the cached active models URL
        active_models_url = "https://active.mor.org/active_models.json"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                active_models_url,
                timeout=10.0
            )
            response.raise_for_status()
            
            data = response.json()
            active_models = data.get("models", [])
            
            # Convert blockchain models to OpenAI format with required fields
            models = []
            for model in active_models:
                model_name = model.get("Name", "unknown-model")
                blockchain_id = model.get("Id", "")
                created_timestamp = model.get("CreatedAt", int(time.time()))
                
                # Get model tags
                tags = model.get("Tags", [])
                
                # Create simplified OpenAI-compatible model
                openai_model = {
                    "id": model_name,
                    "blockchainID": blockchain_id,
                    "created": created_timestamp,
                    "tags": tags
                }
                
                models.append(openai_model)
            
            return {"object": "list", "data": models}
    except httpx.HTTPStatusError as e:
        # Handle HTTP errors and return detailed error messages
        import logging
        logging.error(f"HTTP error getting active models: {e}")
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
            detail=f"Error fetching active models: {detail_message}"
        )
    except Exception as e:
        # Handle other errors
        import logging
        logging.error(f"Error getting active models: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching active models: {str(e)}"
        )

@router.get("/allmodels", response_model=None)
async def list_all_models():
    """
    Get a list of all available models.
    
    Response is in OpenAI API format with selected fields from the blockchain data.
    Only returns non-deleted models.
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
            
            # Filter out deleted models
            active_models = [model for model in blockchain_models if not model.get("IsDeleted", False)]
            
            # Convert blockchain models to OpenAI format with required fields
            models = []
            for model in active_models:
                model_name = model.get("Name", "unknown-model")
                blockchain_id = model.get("Id", "")
                created_timestamp = model.get("CreatedAt", int(time.time()))
                
                # Get model tags
                tags = model.get("Tags", [])
                
                # Create simplified OpenAI-compatible model
                openai_model = {
                    "id": model_name,
                    "blockchainID": blockchain_id,
                    "created": created_timestamp,
                    "tags": tags
                }
                
                models.append(openai_model)
            
            return {"object": "list", "data": models}
    except httpx.HTTPStatusError as e:
        # Handle HTTP errors and return detailed error messages
        import logging
        logging.error(f"HTTP error getting all models: {e}")
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
            detail=f"Error fetching all models from blockchain: {detail_message}"
        )
    except Exception as e:
        # Handle other errors
        import logging
        logging.error(f"Error getting all models: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching all models: {str(e)}"
        )

@router.get("/ratedbids")
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