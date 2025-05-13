import json
import os
from typing import Dict, Optional
import logging

# Configure logger
logger = logging.getLogger(__name__)

# Define a default model to use as fallback
DEFAULT_MODEL = "mistral-31-24b"

class ModelRouter:
    """
    Handles routing of model names to blockchain IDs.
    """
    
    def __init__(self):
        # Initialize with empty mapping
        self._model_mapping: Dict[str, str] = {}
        self._blockchain_ids = set()
        
        # Load models from models.json
        self._load_models_from_json()
        
        # Log available models for diagnostics
        logger.info(f"[MODEL_DEBUG] Initialized ModelRouter with {len(self._model_mapping)} models")
        for model_name, model_id in sorted(self._model_mapping.items()):
            logger.info(f"[MODEL_DEBUG] Mapping: {model_name} -> {model_id}")
    
    def _load_models_from_json(self):
        """Load model mappings from models.json file"""
        try:
            models_file_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'models.json')
            logger.info(f"[MODEL_DEBUG] Loading models from: {models_file_path}")
            
            if not os.path.exists(models_file_path):
                logger.error(f"[MODEL_DEBUG] models.json file not found at: {models_file_path}")
                return
                
            with open(models_file_path, 'r') as f:
                models_data = json.load(f)
                
            # Create mapping from model name to blockchain ID
            loaded_models = 0
            skipped_models = 0
            for model in models_data.get('models', []):
                if not model.get('IsDeleted', False):
                    model_name = model.get('Name')
                    model_id = model.get('Id')
                    if model_name and model_id:
                        self._model_mapping[model_name] = model_id
                        self._blockchain_ids.add(model_id)
                        loaded_models += 1
                    else:
                        logger.warning(f"[MODEL_DEBUG] Skipping model with missing name or ID: {model}")
                        skipped_models += 1
                else:
                    skipped_models += 1
            
            logger.info(f"[MODEL_DEBUG] Loaded {loaded_models} models, skipped {skipped_models} models")
            
            if not self._model_mapping:
                logger.warning("[MODEL_DEBUG] No models found in models.json, using empty mapping")
        except FileNotFoundError:
            logger.error(f"[MODEL_DEBUG] Models file not found")
            logger.warning("[MODEL_DEBUG] Using empty model mapping due to missing file")
        except json.JSONDecodeError as je:
            logger.error(f"[MODEL_DEBUG] JSON parsing error in models.json: {je}")
            logger.warning("[MODEL_DEBUG] Using empty model mapping due to invalid JSON")
        except Exception as e:
            logger.error(f"[MODEL_DEBUG] Error loading models from models.json: {e}")
            logger.exception(e)
            logger.warning("[MODEL_DEBUG] Using empty model mapping due to error")
    
    def get_target_model(self, requested_model: Optional[str]) -> str:
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
            default_id = self._get_default_model_id()
            logger.info(f"[MODEL_DEBUG] Resolved to default model ID: {default_id}")
            return default_id
            
        # If it's already a blockchain ID, validate and return it
        if requested_model.startswith("0x"):
            if requested_model in self._blockchain_ids:
                logger.info(f"[MODEL_DEBUG] Valid blockchain ID provided: {requested_model}")
                return requested_model
            logger.warning(f"[MODEL_DEBUG] Invalid blockchain ID: {requested_model}, not in known IDs: {sorted(list(self._blockchain_ids))}")
            logger.warning(f"[MODEL_DEBUG] Using default model: {DEFAULT_MODEL}")
            default_id = self._get_default_model_id()
            logger.info(f"[MODEL_DEBUG] Resolved to default model ID: {default_id}")
            return default_id
            
        # Look up the model name in our mapping
        target_model = self._model_mapping.get(requested_model)
        
        # If not found, try reloading models.json
        if not target_model:
            logger.warning(f"[MODEL_DEBUG] Model '{requested_model}' not found in current mapping. Attempting to reload models.json.")
            self._load_models_from_json()
            # Try again with fresh mapping
            target_model = self._model_mapping.get(requested_model)
        
        # If still not found, use default model
        if not target_model:
            logger.warning(f"[MODEL_DEBUG] Unknown model name: '{requested_model}', not in known models: {sorted(list(self._model_mapping.keys()))}")
            logger.warning(f"[MODEL_DEBUG] Using default model: {DEFAULT_MODEL}")
            default_id = self._get_default_model_id()
            logger.info(f"[MODEL_DEBUG] Resolved to default model ID: {default_id}")
            return default_id
        
        logger.info(f"[MODEL_DEBUG] Model '{requested_model}' successfully resolved to ID: {target_model}")
        return target_model
    
    def _get_default_model_id(self) -> str:
        """Get the blockchain ID for the default model"""
        # First try the explicitly defined default
        if DEFAULT_MODEL in self._model_mapping:
            logger.info(f"[MODEL_DEBUG] Using configured default model: {DEFAULT_MODEL} -> {self._model_mapping[DEFAULT_MODEL]}")
            return self._model_mapping[DEFAULT_MODEL]
        
        # If that fails, try "default" model
        if "default" in self._model_mapping:
            logger.info(f"[MODEL_DEBUG] Using 'default' model: {self._model_mapping['default']}")
            return self._model_mapping["default"]
            
        # If no default model is found, use the first available model
        if self._model_mapping:
            first_model_name = next(iter(self._model_mapping.keys()))
            first_model = self._model_mapping[first_model_name]
            logger.warning(f"[MODEL_DEBUG] No default model configured, using first available model: {first_model_name} -> {first_model}")
            return first_model
            
        # If there are no models at all, raise an error
        logger.error("[MODEL_DEBUG] No models available in the system, cannot route!")
        raise ValueError("No models available in the system")
    
    def is_valid_model(self, model: str) -> bool:
        """
        Check if a model name or blockchain ID is valid.
        
        Args:
            model: The model name or blockchain ID to validate
            
        Returns:
            bool: True if valid, False otherwise
        """
        if not model:
            return False
            
        if model.startswith("0x"):
            return model in self._blockchain_ids
            
        return model in self._model_mapping
    
    def get_available_models(self) -> Dict[str, str]:
        """
        Get a dictionary of available models and their blockchain IDs.
        
        Returns:
            Dict[str, str]: Dictionary mapping model names to blockchain IDs
        """
        return self._model_mapping.copy()

# Create a singleton instance
model_router = ModelRouter() 