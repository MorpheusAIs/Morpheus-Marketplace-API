from typing import Dict, Optional
from .direct_model_service import direct_model_service
from .config import settings
from .logging_config import get_models_logger

# Configure logger
logger = get_models_logger()

# Get default model from settings
DEFAULT_MODEL = getattr(settings, 'DEFAULT_FALLBACK_MODEL', "mistral-31-24b")

class ModelRouter:
    """
    Handles routing of model names to blockchain IDs using DirectModelService.
    """
    
    def __init__(self):
        logger.info("Initialized ModelRouter with DirectModelService",
                   event_type="model_router_init")
        # No initialization needed - DirectModelService handles all caching
    
    async def get_target_model(self, requested_model: Optional[str]) -> str:
        """
        Get the target blockchain ID for the requested model.
        
        Args:
            requested_model: The model name or blockchain ID requested by the user
            
        Returns:
            str: The blockchain ID to use
        """
        logger.info("Getting target model for requested model",
                   requested_model=requested_model,
                   event_type="model_resolution_start")
        
        if not requested_model:
            logger.warning("No model specified, using default model",
                          default_model=DEFAULT_MODEL,
                          event_type="default_model_fallback")
            default_id = await self._get_default_model_id()
            logger.info("Resolved to default model ID",
                       default_model_id=default_id,
                       event_type="default_model_resolved")
            return default_id
            
        # Try to resolve using DirectModelService
        try:
            resolved_id = await direct_model_service.resolve_model_id(requested_model)
            if resolved_id:
                logger.info("Found model mapping",
                           requested_model=requested_model,
                           resolved_id=resolved_id,
                           event_type="model_resolved")
                return resolved_id
            else:
                # Model not found, use default
                logger.warning("Model not found in active models",
                              requested_model=requested_model,
                              event_type="model_not_found")
                model_mapping = await direct_model_service.get_model_mapping()
                blockchain_ids = await direct_model_service.get_blockchain_ids()
                logger.info("Available models for debugging",
                           available_models=sorted(list(model_mapping.keys())),
                           available_blockchain_ids=sorted(list(blockchain_ids)),
                           requested_model=requested_model)
                
                default_id = await self._get_default_model_id()
                logger.warning("Using default model fallback",
                              requested_model=requested_model,
                              default_model_id=default_id,
                              event_type="default_model_fallback")
                return default_id
        except Exception as e:
            logger.error("Error resolving model - using default fallback",
                        requested_model=requested_model,
                        error=str(e),
                        event_type="model_resolution_error")
            # Fall back to default model
            default_id = await self._get_default_model_id()
            logger.warning("Using default model ID due to error",
                          requested_model=requested_model,
                          default_model_id=default_id,
                          event_type="default_model_error_fallback")
            return default_id
    
    async def _get_default_model_id(self) -> str:
        """Get the blockchain ID for the default model"""
        try:
            model_mapping = await direct_model_service.get_model_mapping()
            
            # First try the explicitly defined default
            if DEFAULT_MODEL in model_mapping:
                logger.info("Using configured default model",
                           default_model=DEFAULT_MODEL,
                           blockchain_id=model_mapping[DEFAULT_MODEL],
                           event_type="default_model_resolved")
                return model_mapping[DEFAULT_MODEL]
            
            # If that fails, try "default" model
            if "default" in model_mapping:
                logger.info("Using 'default' model",
                           blockchain_id=model_mapping['default'],
                           event_type="generic_default_model_used")
                return model_mapping["default"]
                
            # If no default model is found, use the first available model
            if model_mapping:
                first_model_name = next(iter(model_mapping.keys()))
                first_model = model_mapping[first_model_name]
                logger.warning("No default model configured, using first available model",
                              first_model_name=first_model_name,
                              first_model_id=first_model,
                              event_type="first_available_model_fallback")
                return first_model
                
            # If there are no models at all, raise an error
            logger.error("No models available in the system, cannot route",
                       event_type="no_models_available_error")
            raise ValueError("No models available in the system")
        except Exception as e:
            logger.error("Error getting default model",
                        error=str(e),
                        event_type="default_model_fetch_error")
            raise ValueError(f"Error getting default model: {e}")
    
    async def is_valid_model(self, model: str) -> bool:
        """
        Check if a model name or blockchain ID is valid.
        
        Args:
            model: The model name or blockchain ID to validate
            
        Returns:
            bool: True if valid, False otherwise
        """
        if not model:
            return False
        
        try:
            resolved_id = await direct_model_service.resolve_model_id(model)
            return resolved_id is not None
        except Exception as e:
            logger.error("Error validating model",
                        model=model,
                        error=str(e),
                        event_type="model_validation_error")
            return False
    
    async def get_available_models(self) -> Dict[str, str]:
        """
        Get a dictionary of available models and their blockchain IDs.
        
        Returns:
            Dict[str, str]: Dictionary mapping model names to blockchain IDs
        """
        try:
            return await direct_model_service.get_model_mapping()
        except Exception as e:
            logger.error("Error getting available models",
                        error=str(e),
                        event_type="available_models_fetch_error")
            return {}

# Create a singleton instance
model_router = ModelRouter()

# Create an async alias for backward compatibility
async_model_router = model_router 