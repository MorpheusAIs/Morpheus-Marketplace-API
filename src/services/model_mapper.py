from typing import Dict, List, Optional
import time
from datetime import timedelta
import json
import logging
import httpx
import uuid

from ..schemas import openai as openai_schemas
from .redis_client import redis_client
from ..core.config import settings

logger = logging.getLogger(__name__)

# Cache keys
MODELS_LIST_CACHE_KEY = "models:list"
MODEL_DETAIL_CACHE_PREFIX = "models:detail:"
OPENAI_TO_BLOCKCHAIN_MAP_CACHE_KEY = "models:mapping"

# Cache expiration (in seconds)
MODELS_CACHE_TTL = 3600  # 1 hour

# Blockchain model endpoint
BLOCKCHAIN_MODELS_ENDPOINT = f"{settings.PROXY_ROUTER_URL}/blockchain/models"

# Authentication credentials
AUTH = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)

class ModelMapper:
    """
    Service for mapping between OpenAI model names and blockchain model IDs.
    
    Uses Redis for caching model information.
    Fetches actual model data from the Morpheus-Lumerin-Node.
    """
    
    def _convert_blockchain_model_to_openai_format(self, blockchain_model) -> openai_schemas.Model:
        """
        Convert blockchain model data to OpenAI model format.
        
        Args:
            blockchain_model: Model data from blockchain
            
        Returns:
            Model object in OpenAI format
        """
        # Extract blockchain model information
        model_id = blockchain_model.get("Name", "unknown-model")
        created_timestamp = blockchain_model.get("CreatedAt", int(time.time()))
        
        # Create a unique permission ID based on the model ID
        permission_id = f"modelperm-{uuid.uuid4().hex[:8]}"
        
        # Create an OpenAI-compatible model object
        return openai_schemas.Model(
            id=model_id,
            object="model",
            created=created_timestamp,
            owned_by=blockchain_model.get("Owner", "morpheus"),
            root=model_id,
            parent=None,
            permission=[
                openai_schemas.ModelPermission(
                    id=permission_id,
                    object="model_permission",
                    created=created_timestamp,
                    allow_create_engine=False,
                    allow_sampling=True,
                    allow_logprobs=True,
                    allow_search_indices=False,
                    allow_view=True,
                    allow_fine_tuning=False,
                    organization="morpheus",
                    group=None,
                    is_blocking=False
                )
            ]
        )
    
    async def get_all_models(self, force_refresh: bool = False) -> List[openai_schemas.Model]:
        """
        Get all available models from the blockchain.
        
        Args:
            force_refresh: If True, bypass cache and fetch fresh data
            
        Returns:
            List of Model objects
        """
        # Check cache first unless forced to refresh
        if not force_refresh:
            cached_models = redis_client.get(MODELS_LIST_CACHE_KEY)
            if cached_models:
                logger.debug("Returning models from cache")
                return [openai_schemas.Model.parse_obj(model) for model in cached_models]
        
        # Fetch models from the blockchain
        logger.info(f"Fetching models from blockchain at {BLOCKCHAIN_MODELS_ENDPOINT}")
        
        # Use authentication credentials
        async with httpx.AsyncClient() as client:
            response = await client.get(
                BLOCKCHAIN_MODELS_ENDPOINT,
                auth=AUTH,
                timeout=10.0
            )
            response.raise_for_status()
            
            blockchain_data = response.json()
            blockchain_models = blockchain_data.get("models", [])
            
            # Convert blockchain models to OpenAI format
            models = [self._convert_blockchain_model_to_openai_format(model) for model in blockchain_models]
            
            # Cache the result
            try:
                redis_client.set(
                    MODELS_LIST_CACHE_KEY,
                    [model.dict() for model in models],
                    expire=MODELS_CACHE_TTL
                )
            except Exception as e:
                logger.warning(f"Failed to cache models list: {e}")
            
            return models
    
    async def get_model_by_id(self, model_id: str, force_refresh: bool = False) -> Optional[openai_schemas.Model]:
        """
        Get a specific model by ID.
        
        Args:
            model_id: Model ID (e.g., 'gpt-4')
            force_refresh: If True, bypass cache and fetch fresh data
            
        Returns:
            Model object or None if not found
        """
        cache_key = f"{MODEL_DETAIL_CACHE_PREFIX}{model_id}"
        
        # Check cache first unless forced to refresh
        if not force_refresh:
            try:
                cached_model = redis_client.get(cache_key)
                if cached_model:
                    return openai_schemas.Model.parse_obj(cached_model)
            except Exception as e:
                logger.warning(f"Failed to get model from cache: {e}")
        
        # Fetch all models and find the specific one
        all_models = await self.get_all_models(force_refresh=force_refresh)
        for model in all_models:
            if model.id == model_id:
                # Cache the result
                try:
                    redis_client.set(cache_key, model.dict(), expire=MODELS_CACHE_TTL)
                except Exception as e:
                    logger.warning(f"Failed to cache model: {e}")
                
                return model
        
        return None
    
    async def get_blockchain_model_id(self, openai_model_id: str, force_refresh: bool = False) -> Optional[str]:
        """
        Map a model name to its blockchain model ID.
        
        Args:
            openai_model_id: Model name (e.g., 'gpt-4')
            force_refresh: If True, bypass cache and fetch fresh data
            
        Returns:
            Blockchain model ID or None if mapping not found
        """
        # Check cache first unless forced to refresh
        if not force_refresh:
            try:
                cached_mapping = redis_client.get(OPENAI_TO_BLOCKCHAIN_MAP_CACHE_KEY)
                if cached_mapping:
                    return cached_mapping.get(openai_model_id)
            except Exception as e:
                logger.warning(f"Failed to get model mapping from cache: {e}")
        
        # Create mapping from model name to blockchain ID
        model_mapping = {}
        
        # Fetch models from the blockchain
        async with httpx.AsyncClient() as client:
            response = await client.get(
                BLOCKCHAIN_MODELS_ENDPOINT,
                auth=AUTH,
                timeout=10.0
            )
            response.raise_for_status()
            
            blockchain_data = response.json()
            blockchain_models = blockchain_data.get("models", [])
            
            # Create mapping from model name to blockchain ID
            for blockchain_model in blockchain_models:
                model_name = blockchain_model.get("Name", "")
                model_id = blockchain_model.get("Id", "")
                
                if model_name and model_id:
                    model_mapping[model_name] = model_id
        
        # Cache the result
        try:
            redis_client.set(
                OPENAI_TO_BLOCKCHAIN_MAP_CACHE_KEY,
                model_mapping,
                expire=MODELS_CACHE_TTL
            )
        except Exception as e:
            logger.warning(f"Failed to cache model mapping: {e}")
        
        return model_mapping.get(openai_model_id)
    
    async def refresh_all_caches(self) -> None:
        """
        Force refresh all model-related caches.
        
        This would typically be called periodically or when model mappings change.
        """
        try:
            await self.get_all_models(force_refresh=True)
            await self.get_blockchain_model_id("", force_refresh=True)  # This will refresh the mapping cache
        except Exception as e:
            logger.error(f"Failed to refresh caches: {e}")


# Create a singleton instance to be used throughout the application
model_mapper = ModelMapper() 