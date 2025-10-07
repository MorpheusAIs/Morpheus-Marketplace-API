from typing import Dict, List, Optional
import time
import uuid

from ..schemas import openai as openai_schemas
from ..core.config import settings
from ..core.logging_config import get_models_logger
from . import proxy_router_service

logger = get_models_logger()


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
        # Fetch models from the blockchain using SDK
        mapper_logger = logger.bind(endpoint="get_all_models")
        mapper_logger.info("Fetching models from blockchain API using SDK",
                          event_type="blockchain_models_fetch_start")
        
        try:
            # Use SDK to fetch models
            response = await proxy_router_service.getAllModels()
            blockchain_data = response.json()
            blockchain_models = blockchain_data.get("models", [])
            
            # Filter out deleted models
            active_models = [model for model in blockchain_models if not model.get("IsDeleted", False)]
            
            mapper_logger.info("Retrieved models from blockchain",
                              total_models=len(blockchain_models),
                              active_models=len(active_models),
                              deleted_models=len(blockchain_models) - len(active_models),
                              event_type="blockchain_models_retrieved")
            
            # Convert blockchain models to simplified OpenAI format
            models = [self._convert_blockchain_model_to_openai_format(model) for model in active_models]
            
            mapper_logger.info("Successfully converted models to OpenAI format",
                              converted_model_count=len(models),
                              event_type="models_conversion_success")
            return models
            
        except proxy_router_service.ProxyRouterServiceError as e:
            mapper_logger.error("Proxy router service error fetching models",
                               error=str(e),
                               status_code=e.status_code,
                               error_type=e.error_type,
                               event_type="blockchain_models_service_error")
            raise
        except Exception as e:
            mapper_logger.error("Unexpected error fetching models",
                               error=str(e),
                               event_type="blockchain_models_unexpected_error")
            raise
    
    async def get_model_by_id(self, model_id: str) -> Optional[Dict]:
        """
        Get a specific model by ID.
        
        Args:
            model_id: Model ID (e.g., 'gpt-4')
            
        Returns:
            Model object or None if not found
        """
        # Fetch all models and find the specific one
        get_model_logger = logger.bind(endpoint="get_model_by_id", model_id=model_id)
        get_model_logger.info("Looking up specific model",
                             requested_model_id=model_id,
                             event_type="model_lookup_start")
        
        all_models = await self.get_all_models()
        for model in all_models:
            if model["id"] == model_id:
                get_model_logger.info("Model found successfully",
                                     model_id=model_id,
                                     blockchain_id=model.get("blockchainID"),
                                     event_type="model_lookup_success")
                return model
        
        get_model_logger.warning("Model not found",
                                model_id=model_id,
                                total_models_searched=len(all_models),
                                event_type="model_lookup_not_found")
        return None
    
    async def get_blockchain_model_id(self, openai_model_id: str) -> Optional[str]:
        """
        Map a model name to its blockchain model ID.
        
        Args:
            openai_model_id: Model name (e.g., 'gpt-4')
            
        Returns:
            Blockchain model ID or None if mapping not found
        """
        # Find the model with matching name
        mapping_logger = logger.bind(endpoint="get_blockchain_model_id", 
                                    openai_model_id=openai_model_id)
        mapping_logger.info("Searching for blockchain model ID mapping using SDK",
                           openai_model_id=openai_model_id,
                           event_type="blockchain_id_mapping_start")
        
        try:
            # Use SDK to fetch models
            response = await proxy_router_service.getAllModels()
            blockchain_data = response.json()
            blockchain_models = blockchain_data.get("models", [])
            
            # Filter out deleted models
            active_models = [model for model in blockchain_models if not model.get("IsDeleted", False)]
            
            mapping_logger.info("Retrieved models for mapping search",
                               total_active_models=len(active_models),
                               event_type="blockchain_models_retrieved_for_mapping")
            
            for blockchain_model in active_models:
                model_name = blockchain_model.get("Name", "")
                model_id = blockchain_model.get("Id", "")
                
                if model_name == openai_model_id and model_id:
                    mapping_logger.info("Found blockchain model ID mapping",
                                       openai_model_id=openai_model_id,
                                       blockchain_id=model_id,
                                       event_type="blockchain_id_mapping_found")
                    return model_id
            
            mapping_logger.warning("No blockchain model ID mapping found",
                                  openai_model_id=openai_model_id,
                                  total_models_searched=len(active_models),
                                  event_type="blockchain_id_mapping_not_found")
            return None
            
        except proxy_router_service.ProxyRouterServiceError as e:
            mapping_logger.error("Proxy router service error during model ID mapping",
                                 error=str(e),
                                 status_code=e.status_code,
                                 error_type=e.error_type,
                                 openai_model_id=openai_model_id,
                                 event_type="blockchain_id_mapping_service_error")
            raise
        except Exception as e:
            mapping_logger.error("Unexpected error during model ID mapping",
                                 error=str(e),
                                 openai_model_id=openai_model_id,
                                 event_type="blockchain_id_mapping_unexpected_error")
            raise


# Create a singleton instance to be used throughout the application
model_mapper = ModelMapper() 