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
    
    def _load_models_from_json(self):
        """Load model mappings from models.json file"""
        try:
            models_file_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'models.json')
            
            with open(models_file_path, 'r') as f:
                models_data = json.load(f)
                
            # Create mapping from model name to blockchain ID
            for model in models_data.get('models', []):
                if not model.get('IsDeleted', False):
                    model_name = model.get('Name')
                    model_id = model.get('Id')
                    if model_name and model_id:
                        self._model_mapping[model_name] = model_id
                        self._blockchain_ids.add(model_id)
            
            if not self._model_mapping:
                logger.warning("No models found in models.json, using empty mapping")
        except Exception as e:
            logger.error(f"Error loading models from models.json: {e}")
            logger.warning("Using empty model mapping due to error")
    
    def get_target_model(self, requested_model: Optional[str]) -> str:
        """
        Get the target blockchain ID for the requested model.
        
        Args:
            requested_model: The model name or blockchain ID requested by the user
            
        Returns:
            str: The blockchain ID to use
        """
        if not requested_model:
            logger.warning(f"No model specified, using default model: {DEFAULT_MODEL}")
            return self._get_default_model_id()
            
        # If it's already a blockchain ID, validate and return it
        if requested_model.startswith("0x"):
            if requested_model in self._blockchain_ids:
                return requested_model
            logger.warning(f"Invalid blockchain ID: {requested_model}, using default model: {DEFAULT_MODEL}")
            return self._get_default_model_id()
            
        # Look up the model name in our mapping
        target_model = self._model_mapping.get(requested_model)
        
        # If not found, use default model
        if not target_model:
            logger.warning(f"Unknown model name: {requested_model}, using default model: {DEFAULT_MODEL}")
            return self._get_default_model_id()
            
        return target_model
    
    def _get_default_model_id(self) -> str:
        """Get the blockchain ID for the default model"""
        # First try the explicitly defined default
        if DEFAULT_MODEL in self._model_mapping:
            return self._model_mapping[DEFAULT_MODEL]
        
        # If that fails, try "default" model
        if "default" in self._model_mapping:
            return self._model_mapping["default"]
            
        # If no default model is found, use the first available model
        if self._model_mapping:
            first_model = next(iter(self._model_mapping.values()))
            logger.warning(f"No default model configured, using first available model: {first_model}")
            return first_model
            
        # If there are no models at all, raise an error
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