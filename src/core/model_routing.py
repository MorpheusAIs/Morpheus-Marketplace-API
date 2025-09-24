from typing import Dict, Optional
from .direct_model_service import direct_model_service
from .config import settings
from .structured_logger import MODELS_LOG

# Configure structured logger (Models category)
model_router_log = MODELS_LOG.named("MODEL_ROUTER")

# Get default model from settings
DEFAULT_MODEL = getattr(settings, 'DEFAULT_FALLBACK_MODEL', "mistral-31-24b")

class ModelRouter:
    """
    Handles routing of model names to blockchain IDs using DirectModelService.
    """
    
    def __init__(self):
        model_router_log.with_fields(
            event_type="model_router_init"
        ).info("Initialized ModelRouter with DirectModelService")
        # No initialization needed - DirectModelService handles all caching
    
    async def get_target_model(self, requested_model: Optional[str]) -> str:
        """
        Get the target blockchain ID for the requested model.
        
        Args:
            requested_model: The model name or blockchain ID requested by the user
            
        Returns:
            str: The blockchain ID to use
        """
        model_router_log.with_fields(
            event_type="model_resolution",
            requested_model=requested_model
        ).infof("Getting target model for requested model: '%s'", requested_model)
        
        if not requested_model:
            model_router_log.with_fields(
                event_type="model_resolution",
                fallback_reason="no_model_specified",
                default_model=DEFAULT_MODEL
            ).warnf("No model specified, using default model: %s", DEFAULT_MODEL)
            default_id = await self._get_default_model_id()
            model_router_log.with_fields(
                event_type="model_resolution",
                resolved_id=default_id,
                resolution_type="default"
            ).infof("Resolved to default model ID: %s", default_id)
            return default_id
            
        # Try to resolve using DirectModelService
        try:
            resolved_id = await direct_model_service.resolve_model_id(requested_model)
            if resolved_id:
                model_router_log.with_fields(
                    event_type="model_resolution",
                    requested_model=requested_model,
                    resolved_id=resolved_id,
                    resolution_type="direct_mapping"
                ).infof("Found mapping: %s -> %s", requested_model, resolved_id)
                return resolved_id
            else:
                # Model not found, use default
                model_router_log.with_fields(
                    event_type="model_resolution",
                    requested_model=requested_model,
                    fallback_reason="model_not_found"
                ).warnf("Model '%s' not found in active models", requested_model)
                model_mapping = await direct_model_service.get_model_mapping()
                blockchain_ids = await direct_model_service.get_blockchain_ids()
                model_router_log.with_fields(
                    event_type="model_debug",
                    available_models=sorted(list(model_mapping.keys()))
                ).warnf("Available models: %s", sorted(list(model_mapping.keys())))
                model_router_log.with_fields(
                    event_type="model_debug",
                    available_blockchain_ids=sorted(list(blockchain_ids))
                ).warnf("Available blockchain IDs: %s", sorted(list(blockchain_ids)))
                model_router_log.with_fields(
                    event_type="model_resolution",
                    fallback_reason="model_not_found",
                    default_model=DEFAULT_MODEL
                ).warnf("Using default model: %s", DEFAULT_MODEL)
                default_id = await self._get_default_model_id()
                model_router_log.with_fields(
                    event_type="model_resolution",
                    resolved_id=default_id,
                    resolution_type="fallback_default"
                ).infof("Resolved to default model ID: %s", default_id)
                return default_id
        except Exception as e:
            model_router_log.with_fields(
                event_type="model_resolution_error",
                requested_model=requested_model,
                error=str(e)
            ).errorf("Error resolving model '%s': %s", requested_model, e)
            model_router_log.with_fields(
                event_type="model_resolution",
                fallback_reason="resolution_error",
                default_model=DEFAULT_MODEL
            ).warnf("Using default model: %s", DEFAULT_MODEL)
            default_id = await self._get_default_model_id()
            model_router_log.with_fields(
                event_type="model_resolution",
                resolved_id=default_id,
                resolution_type="error_fallback"
            ).infof("Resolved to default model ID: %s", default_id)
            return default_id
    
    async def _get_default_model_id(self) -> str:
        """Get the blockchain ID for the default model"""
        try:
            model_mapping = await direct_model_service.get_model_mapping()
            
            # First try the explicitly defined default
            if DEFAULT_MODEL in model_mapping:
                model_router_log.with_fields(
                    event_type="default_model_selection",
                    default_model=DEFAULT_MODEL,
                    resolved_id=model_mapping[DEFAULT_MODEL],
                    selection_type="configured_default"
                ).infof("Using configured default model: %s -> %s", DEFAULT_MODEL, model_mapping[DEFAULT_MODEL])
                return model_mapping[DEFAULT_MODEL]
            
            # If that fails, try "default" model
            if "default" in model_mapping:
                model_router_log.with_fields(
                    event_type="default_model_selection",
                    resolved_id=model_mapping["default"],
                    selection_type="generic_default"
                ).infof("Using 'default' model: %s", model_mapping["default"])
                return model_mapping["default"]
                
            # If no default model is found, use the first available model
            if model_mapping:
                first_model_name = next(iter(model_mapping.keys()))
                first_model = model_mapping[first_model_name]
                model_router_log.with_fields(
                    event_type="default_model_selection",
                    first_model_name=first_model_name,
                    resolved_id=first_model,
                    selection_type="first_available"
                ).warnf("No default model configured, using first available model: %s -> %s", first_model_name, first_model)
                return first_model
                
            # If there are no models at all, raise an error
            model_router_log.with_fields(
                event_type="default_model_error",
                error="no_models_available"
            ).error("No models available in the system, cannot route!")
            raise ValueError("No models available in the system")
        except Exception as e:
            model_router_log.with_fields(
                event_type="default_model_error",
                error=str(e)
            ).errorf("Error getting default model: %s", e)
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
            model_router_log.with_fields(
                event_type="model_validation_error",
                model=model,
                error=str(e)
            ).errorf("Error validating model '%s': %s", model, e)
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
            model_router_log.with_fields(
                event_type="get_models_error",
                error=str(e)
            ).errorf("Error getting available models: %s", e)
            return {}

# Create a singleton instance
model_router = ModelRouter()

# Create an async alias for backward compatibility
async_model_router = model_router 