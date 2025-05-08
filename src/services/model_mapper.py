from typing import Dict, List, Optional
import time
import logging
import httpx
import uuid

from ..schemas import openai as openai_schemas
from ..core.config import settings

logger = logging.getLogger(__name__)

# Blockchain model endpoint
BLOCKCHAIN_MODELS_ENDPOINT = f"{settings.PROXY_ROUTER_URL}/blockchain/models"

# Authentication credentials
AUTH = (settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD)

class ModelMapper:
    """
    Service for mapping between OpenAI model names and blockchain model IDs.
    
    Fetches model data directly from the Morpheus-Lumerin-Node.
    """
    
    def _convert_blockchain_model_to_openai_format(self, blockchain_model) -> Dict:
        """
        Convert blockchain model data to simplified OpenAI format.
        
        Args:
            blockchain_model: Model data from blockchain
            
        Returns:
            Model object in simplified OpenAI format
        """
        # Extract blockchain model information
        model_id = blockchain_model.get("Name", "unknown-model")
        blockchain_id = blockchain_model.get("Id", "")
        created_timestamp = blockchain_model.get("CreatedAt", int(time.time()))
        tags = blockchain_model.get("Tags", [])
        
        # Create a simplified OpenAI-compatible model object
        return {
            "id": model_id,
            "blockchainID": blockchain_id,
            "created": created_timestamp,
            "tags": tags
        }
    
    async def get_all_models(self) -> List[Dict]:
        """
        Get all available models from the blockchain.
        
        Returns:
            List of model objects in simplified format
        """
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
            
            # Filter out deleted models
            active_models = [model for model in blockchain_models if not model.get("IsDeleted", False)]
            
            # Convert blockchain models to simplified OpenAI format
            models = [self._convert_blockchain_model_to_openai_format(model) for model in active_models]
            
            return models
    
    async def get_model_by_id(self, model_id: str) -> Optional[Dict]:
        """
        Get a specific model by ID.
        
        Args:
            model_id: Model ID (e.g., 'gpt-4')
            
        Returns:
            Model object or None if not found
        """
        # Fetch all models and find the specific one
        all_models = await self.get_all_models()
        for model in all_models:
            if model["id"] == model_id:
                return model
        
        return None
    
    async def get_blockchain_model_id(self, openai_model_id: str) -> Optional[str]:
        """
        Map a model name to its blockchain model ID.
        
        Args:
            openai_model_id: Model name (e.g., 'gpt-4')
            
        Returns:
            Blockchain model ID or None if mapping not found
        """
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
            
            # Filter out deleted models
            active_models = [model for model in blockchain_models if not model.get("IsDeleted", False)]
            
            # Find the model with matching name
            for blockchain_model in active_models:
                model_name = blockchain_model.get("Name", "")
                model_id = blockchain_model.get("Id", "")
                
                if model_name == openai_model_id and model_id:
                    return model_id
        
        return None


# Create a singleton instance to be used throughout the application
model_mapper = ModelMapper() 