from typing import Dict, List, Optional
import time
from datetime import timedelta
import json

from ..schemas import openai as openai_schemas
from .redis_client import redis_client

# Cache keys
MODELS_LIST_CACHE_KEY = "models:list"
MODEL_DETAIL_CACHE_PREFIX = "models:detail:"
OPENAI_TO_BLOCKCHAIN_MAP_CACHE_KEY = "models:mapping"

# Cache expiration (in seconds)
MODELS_CACHE_TTL = 3600  # 1 hour

# Placeholder model data until real data source is implemented
PLACEHOLDER_MODELS = [
    {
        "id": "gpt-4",
        "object": "model",
        "created": int(time.time()) - 10000,
        "owned_by": "morpheus",
        "root": "gpt-4",
        "parent": None,
        "permission": [
            {
                "id": "modelperm-123",
                "object": "model_permission",
                "created": int(time.time()) - 10000,
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
    },
    {
        "id": "gpt-3.5-turbo",
        "object": "model",
        "created": int(time.time()) - 20000,
        "owned_by": "morpheus",
        "root": "gpt-3.5-turbo",
        "parent": None,
        "permission": [
            {
                "id": "modelperm-456",
                "object": "model_permission",
                "created": int(time.time()) - 20000,
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
]

# Placeholder model mapping data
PLACEHOLDER_MODEL_MAPPING = {
    "gpt-4": "blockchain-model-id-1",
    "gpt-3.5-turbo": "blockchain-model-id-2"
}


class ModelMapper:
    """
    Service for mapping between OpenAI model names and blockchain model IDs.
    
    Uses Redis for caching model information.
    In production, this would fetch from a more authoritative source.
    """
    
    async def get_all_models(self, force_refresh: bool = False) -> List[openai_schemas.Model]:
        """
        Get all available models.
        
        Args:
            force_refresh: If True, bypass cache and fetch fresh data
            
        Returns:
            List of Model objects
        """
        # Check cache first unless forced to refresh
        if not force_refresh:
            cached_models = redis_client.get(MODELS_LIST_CACHE_KEY)
            if cached_models:
                return [openai_schemas.Model.parse_obj(model) for model in cached_models]
        
        # In a real implementation, this would fetch from a more authoritative source
        # For now, we'll use the placeholder data
        models = [openai_schemas.Model.parse_obj(model) for model in PLACEHOLDER_MODELS]
        
        # Cache the result
        redis_client.set(
            MODELS_LIST_CACHE_KEY,
            [model.dict() for model in models],
            expire=MODELS_CACHE_TTL
        )
        
        return models
    
    async def get_model_by_id(self, model_id: str, force_refresh: bool = False) -> Optional[openai_schemas.Model]:
        """
        Get a specific model by ID.
        
        Args:
            model_id: OpenAI model ID (e.g., 'gpt-4')
            force_refresh: If True, bypass cache and fetch fresh data
            
        Returns:
            Model object or None if not found
        """
        cache_key = f"{MODEL_DETAIL_CACHE_PREFIX}{model_id}"
        
        # Check cache first unless forced to refresh
        if not force_refresh:
            cached_model = redis_client.get(cache_key)
            if cached_model:
                return openai_schemas.Model.parse_obj(cached_model)
        
        # Not in cache or forced refresh, check all models
        for model_data in PLACEHOLDER_MODELS:
            if model_data["id"] == model_id:
                model = openai_schemas.Model.parse_obj(model_data)
                
                # Cache the result
                redis_client.set(cache_key, model.dict(), expire=MODELS_CACHE_TTL)
                
                return model
        
        return None
    
    async def get_blockchain_model_id(self, openai_model_id: str, force_refresh: bool = False) -> Optional[str]:
        """
        Map an OpenAI model ID to a blockchain model ID.
        
        Args:
            openai_model_id: OpenAI model ID (e.g., 'gpt-4')
            force_refresh: If True, bypass cache and fetch fresh data
            
        Returns:
            Blockchain model ID or None if mapping not found
        """
        # Check cache first unless forced to refresh
        if not force_refresh:
            cached_mapping = redis_client.get(OPENAI_TO_BLOCKCHAIN_MAP_CACHE_KEY)
            if cached_mapping:
                return cached_mapping.get(openai_model_id)
        
        # In a real implementation, this would fetch from a more authoritative source
        # For now, we'll use the placeholder mapping
        
        # Cache the result
        redis_client.set(
            OPENAI_TO_BLOCKCHAIN_MAP_CACHE_KEY,
            PLACEHOLDER_MODEL_MAPPING,
            expire=MODELS_CACHE_TTL
        )
        
        return PLACEHOLDER_MODEL_MAPPING.get(openai_model_id)
    
    async def refresh_all_caches(self) -> None:
        """
        Force refresh all model-related caches.
        
        This would typically be called periodically or when model mappings change.
        """
        await self.get_all_models(force_refresh=True)
        
        # Also refresh the model mappings
        redis_client.set(
            OPENAI_TO_BLOCKCHAIN_MAP_CACHE_KEY,
            PLACEHOLDER_MODEL_MAPPING,
            expire=MODELS_CACHE_TTL
        )


# Create a singleton instance to be used throughout the application
model_mapper = ModelMapper() 