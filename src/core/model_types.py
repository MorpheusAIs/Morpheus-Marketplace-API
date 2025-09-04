"""
Model type classification and endpoint routing system.
Determines the appropriate API endpoint and UI behavior based on model tags and capabilities.
"""

from enum import Enum
from typing import List, Dict, Optional, Set
import logging

logger = logging.getLogger(__name__)

class ModelType(Enum):
    """Enumeration of supported model types"""
    CHAT = "chat"
    EMBEDDINGS = "embeddings" 
    TEXT_TO_SPEECH = "text_to_speech"
    SPEECH_TO_TEXT = "speech_to_text"
    IMAGE_GENERATION = "image_generation"
    AUDIO_GENERATION = "audio_generation"
    VIDEO_GENERATION = "video_generation"
    CODE_GENERATION = "code_generation"
    UNKNOWN = "unknown"

class ModelCapability:
    """Represents a model's capabilities and UI requirements"""
    
    def __init__(
        self,
        model_type: ModelType,
        endpoint: str,
        ui_category: str,
        ui_description: str,
        input_types: List[str],
        output_types: List[str],
        supports_streaming: bool = False,
        supports_chat_interface: bool = False,
        requires_file_upload: bool = False
    ):
        self.model_type = model_type
        self.endpoint = endpoint
        self.ui_category = ui_category
        self.ui_description = ui_description
        self.input_types = input_types
        self.output_types = output_types
        self.supports_streaming = supports_streaming
        self.supports_chat_interface = supports_chat_interface
        self.requires_file_upload = requires_file_upload

# Model type classification rules based on tags
MODEL_TYPE_RULES = {
    # Embeddings models
    frozenset(["Embeddings"]): ModelCapability(
        model_type=ModelType.EMBEDDINGS,
        endpoint="/api/v1/embeddings",
        ui_category="Text Processing",
        ui_description="Convert text to numerical vectors for similarity search and analysis",
        input_types=["text"],
        output_types=["embeddings"],
        supports_streaming=False,
        supports_chat_interface=False
    ),
    
    # Text-to-Speech models
    frozenset(["TTS", "tts"]): ModelCapability(
        model_type=ModelType.TEXT_TO_SPEECH,
        endpoint="/api/v1/audio/speech",  # Future endpoint
        ui_category="Audio Generation",
        ui_description="Convert text to natural-sounding speech",
        input_types=["text"],
        output_types=["audio"],
        supports_streaming=True,
        supports_chat_interface=False
    ),
    
    # Speech-to-Text models
    frozenset(["transcribe", "s2t", "speech"]): ModelCapability(
        model_type=ModelType.SPEECH_TO_TEXT,
        endpoint="/api/v1/audio/transcriptions",  # Future endpoint
        ui_category="Audio Processing", 
        ui_description="Convert speech audio to text transcription",
        input_types=["audio"],
        output_types=["text"],
        supports_streaming=False,
        supports_chat_interface=False,
        requires_file_upload=True
    ),
    
    # Enhanced Chat models (web search enabled)
    frozenset(["web-search-enabled"]): ModelCapability(
        model_type=ModelType.CHAT,
        endpoint="/api/v1/chat/completions",
        ui_category="Enhanced Chat",
        ui_description="AI chat with real-time web search capabilities",
        input_types=["text"],
        output_types=["text"],
        supports_streaming=True,
        supports_chat_interface=True
    )
}

# Fallback rules for single tags
SINGLE_TAG_RULES = {
    "Embeddings": ModelType.EMBEDDINGS,
    "TTS": ModelType.TEXT_TO_SPEECH,
    "tts": ModelType.TEXT_TO_SPEECH,
    "transcribe": ModelType.SPEECH_TO_TEXT,
    "s2t": ModelType.SPEECH_TO_TEXT,
    "speech": ModelType.SPEECH_TO_TEXT,
    "textgeneration": ModelType.CHAT,
    "text2text": ModelType.CHAT,
}

def classify_model_type(tags: List[str], model_name: str = "") -> ModelCapability:
    """
    Classify a model based on its tags and return its capabilities.
    
    Args:
        tags: List of model tags
        model_name: Optional model name for additional context
        
    Returns:
        ModelCapability object describing the model's type and requirements
    """
    if not tags:
        # Default to chat if no tags
        return ModelCapability(
            model_type=ModelType.CHAT,
            endpoint="/api/v1/chat/completions",
            ui_category="Chat",
            ui_description="General purpose AI chat model",
            input_types=["text"],
            output_types=["text"],
            supports_streaming=True,
            supports_chat_interface=True
        )
    
    tags_set = set(tags)
    
    # Check exact tag combinations first
    for rule_tags, capability in MODEL_TYPE_RULES.items():
        if rule_tags.issubset(tags_set):
            logger.info(f"Model classified by tag combination {rule_tags}: {capability.model_type}")
            return capability
    
    # Check individual tags
    for tag in tags:
        if tag in SINGLE_TAG_RULES:
            model_type = SINGLE_TAG_RULES[tag]
            logger.info(f"Model classified by single tag '{tag}': {model_type}")
            
            # Return appropriate capability based on type
            if model_type == ModelType.EMBEDDINGS:
                return MODEL_TYPE_RULES[frozenset(["Embeddings"])]
            elif model_type == ModelType.TEXT_TO_SPEECH:
                return MODEL_TYPE_RULES[frozenset(["TTS", "tts"])]
            elif model_type == ModelType.SPEECH_TO_TEXT:
                return MODEL_TYPE_RULES[frozenset(["transcribe", "s2t", "speech"])]
    
    # Default to chat model
    logger.info(f"Model with tags {tags} defaulted to chat type")
    return ModelCapability(
        model_type=ModelType.CHAT,
        endpoint="/api/v1/chat/completions",
        ui_category="Chat",
        ui_description="AI chat and text generation model",
        input_types=["text"],
        output_types=["text"],
        supports_streaming=True,
        supports_chat_interface=True
    )

def get_models_by_type(models: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Group models by their type for UI filtering.
    
    Args:
        models: List of model dictionaries with Name and Tags
        
    Returns:
        Dictionary mapping UI categories to lists of models
    """
    categorized = {}
    
    for model in models:
        tags = model.get("Tags", [])
        name = model.get("Name", "")
        
        capability = classify_model_type(tags, name)
        category = capability.ui_category
        
        if category not in categorized:
            categorized[category] = []
        
        # Add capability info to model
        model_with_capability = model.copy()
        model_with_capability["model_type"] = capability.model_type.value
        model_with_capability["endpoint"] = capability.endpoint
        model_with_capability["ui_category"] = capability.ui_category
        model_with_capability["ui_description"] = capability.ui_description
        model_with_capability["supports_streaming"] = capability.supports_streaming
        model_with_capability["supports_chat_interface"] = capability.supports_chat_interface
        model_with_capability["requires_file_upload"] = capability.requires_file_upload
        
        categorized[category].append(model_with_capability)
    
    return categorized

def get_ui_filters() -> List[Dict[str, str]]:
    """
    Get available UI filter categories.
    
    Returns:
        List of filter options for the frontend
    """
    return [
        {"value": "all", "label": "All Models", "description": "Show all available models"},
        {"value": "Chat", "label": "Chat Models", "description": "Text generation and conversation"},
        {"value": "Enhanced Chat", "label": "Web-Enhanced Chat", "description": "Chat with web search"},
        {"value": "Text Processing", "label": "Text Processing", "description": "Embeddings and analysis"},
        {"value": "Audio Generation", "label": "Audio Generation", "description": "Text-to-speech models"},
        {"value": "Audio Processing", "label": "Audio Processing", "description": "Speech-to-text models"},
        {"value": "Image Generation", "label": "Image Generation", "description": "Text-to-image models"},
        {"value": "Code Generation", "label": "Code Generation", "description": "Code generation and assistance"},
    ]
