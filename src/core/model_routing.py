import json
import os
from typing import Dict, Optional
import logging

# Default mappings only used as a fallback if config file doesn't exist or has errors
DEFAULT_MODEL_MAPPINGS = {
    "default": "0x8f9f631f647b318e720ec00e6aaeeaa60ca2c52db9362a292d44f217e66aa04f",
    "gpt-3.5-turbo": "0xfe4cc20404f223f336f241fa16748b91e8ff1d54141203b0882b637ead9fef79",
    "gpt-4": "0x8f9f631f647b318e720ec00e6aaeeaa60ca2c52db9362a292d44f217e66aa04f",
    "gpt-4o": "0x8f9f631f647b318e720ec00e6aaeeaa60ca2c52db9362a292d44f217e66aa04f"
}

# Configure logger
logger = logging.getLogger(__name__)

class ModelRouter:
    def __init__(self):
        self.mappings = {}
        self.load_mappings()
    
    def load_mappings(self):
        """Load model mappings from config file or use defaults"""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'model_mappings.json')
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    self.mappings = json.load(f)
                logger.info(f"Loaded model mappings from {config_path}")
                # Quick validation of required mappings
                if "default" not in self.mappings:
                    logger.warning("No 'default' mapping found in config, using fallback default")
                    self.mappings["default"] = DEFAULT_MODEL_MAPPINGS["default"]
            else:
                self.mappings = DEFAULT_MODEL_MAPPINGS
                logger.warning(f"Config file not found at {config_path}, using default mappings")
        except Exception as e:
            # Log error and fall back to defaults
            logger.error(f"Error loading model mappings: {e}")
            self.mappings = DEFAULT_MODEL_MAPPINGS
    
    def get_target_model(self, requested_model: Optional[str]) -> str:
        """Get target model ID for the requested model name"""
        logger.info(f"Model routing request received for model: {requested_model}")
        
        if not requested_model:
            # If no model requested, use default
            default_model = self.mappings.get("default")
            if not default_model:
                logger.error("No default model mapping found, check configuration")
                # Emergency fallback to prevent crashes
                logger.info(f"Using emergency fallback default: {DEFAULT_MODEL_MAPPINGS['default']}")
                return DEFAULT_MODEL_MAPPINGS["default"]
            logger.info(f"No model specified, using default: {default_model}")
            return default_model
        
        # Try to get mapping or fall back to default
        target_model = self.mappings.get(requested_model)
        if target_model:
            logger.info(f"Found mapping for {requested_model} to {target_model}")
            return target_model
        
        # No mapping found, use default
        default_model = self.mappings.get("default")
        if not default_model:
            logger.error("No default model mapping found, check configuration")
            # Emergency fallback to prevent crashes
            logger.info(f"Using emergency fallback default: {DEFAULT_MODEL_MAPPINGS['default']}")
            return DEFAULT_MODEL_MAPPINGS["default"]
            
        logger.warning(f"No mapping found for {requested_model}, using default: {default_model}")
        
        # Log available mappings to help diagnose issues
        logger.info(f"Available mappings: {list(self.mappings.keys())}")
        
        return default_model
    
    def reload_mappings(self):
        """Reload mappings from the config file"""
        self.load_mappings()
        return self.mappings

# Create singleton instance
model_router = ModelRouter() 