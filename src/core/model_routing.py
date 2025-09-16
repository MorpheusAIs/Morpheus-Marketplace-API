from typing import Dict, Optional
import logging
from .direct_model_service import direct_model_service
from .config import settings

# Configure logger
logger = logging.getLogger(__name__)

# Get default model from settings
DEFAULT_MODEL = getattr(settings, 'DEFAULT_FALLBACK_MODEL', "mistral-31-24b")

class ModelRouter:
    """
    Handles routing of model names to blockchain IDs using DirectModelService.
    """
    
    def __init__(self):
        logger.info("[MODEL_DEBUG] Initialized ModelRouter with DirectModelService")
        # No initialization needed - DirectModelService handles all caching
    
    async def get_target_model(self, requested_model: Optional[str]) -> str:
        """
        Get the target blockchain ID for the requested model.
        
        Args:
            requested_model: The model name or blockchain ID requested by the user
            
        Returns:
            str: The blockchain ID to use
        """
        logger.info(f"[MODEL_DEBUG] Getting target model for requested model: '{requested_model}'")
        
        if not requested_model:
            logger.warning(f"[MODEL_DEBUG] No model specified, using default model: {DEFAULT_MODEL}")
            default_id = await self._get_default_model_id()
            logger.info(f"[MODEL_DEBUG] Resolved to default model ID: {default_id}")
            return default_id
            
        # Try to resolve using DirectModelService
        try:
            resolved_id = await direct_model_service.resolve_model_id(requested_model)
            if resolved_id:
                logger.info(f"[MODEL_DEBUG] Found mapping: {requested_model} -> {resolved_id}")
                return resolved_id
            else:
                # Model not found, use default
                logger.warning(f"[MODEL_DEBUG] Model '{requested_model}' not found in active models")
                model_mapping = await direct_model_service.get_model_mapping()
                blockchain_ids = await direct_model_service.get_blockchain_ids()
                logger.warning(f"[MODEL_DEBUG] Available models: {sorted(list(model_mapping.keys()))}")
                logger.warning(f"[MODEL_DEBUG] Available blockchain IDs: {sorted(list(blockchain_ids))}")
                logger.warning(f"[MODEL_DEBUG] Using default model: {DEFAULT_MODEL}")
                default_id = await self._get_default_model_id()
                logger.info(f"[MODEL_DEBUG] Resolved to default model ID: {default_id}")
                return default_id
        except Exception as e:
            logger.error(f"[MODEL_DEBUG] Error resolving model '{requested_model}': {e}")
            logger.warning(f"[MODEL_DEBUG] Using default model: {DEFAULT_MODEL}")
            default_id = await self._get_default_model_id()
            logger.info(f"[MODEL_DEBUG] Resolved to default model ID: {default_id}")
            return default_id
    
    async def _get_default_model_id(self) -> str:
        """Get the blockchain ID for the default model"""
        try:
            model_mapping = await direct_model_service.get_model_mapping()
            
            # First try the explicitly defined default
            if DEFAULT_MODEL in model_mapping:
                logger.info(f"[MODEL_DEBUG] Using configured default model: {DEFAULT_MODEL} -> {model_mapping[DEFAULT_MODEL]}")
                return model_mapping[DEFAULT_MODEL]
            
            # If that fails, try "default" model
            if "default" in model_mapping:
                logger.info(f"[MODEL_DEBUG] Using 'default' model: {model_mapping['default']}")
                return model_mapping["default"]
                
            # If no default model is found, use the first available model
            if model_mapping:
                first_model_name = next(iter(model_mapping.keys()))
                first_model = model_mapping[first_model_name]
                logger.warning(f"[MODEL_DEBUG] No default model configured, using first available model: {first_model_name} -> {first_model}")
                return first_model
                
            # If there are no models at all, raise an error
            logger.error("[MODEL_DEBUG] No models available in the system, cannot route!")
            raise ValueError("No models available in the system")
        except Exception as e:
            logger.error(f"[MODEL_DEBUG] Error getting default model: {e}")
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
            logger.error(f"[MODEL_DEBUG] Error validating model '{model}': {e}")
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
            logger.error(f"[MODEL_DEBUG] Error getting available models: {e}")
            return {}

# Create a singleton instance
model_router = ModelRouter()

# Create an async alias for backward compatibility
async_model_router = model_router 