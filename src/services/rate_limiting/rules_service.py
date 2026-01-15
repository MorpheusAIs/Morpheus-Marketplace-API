"""
Rate Limit Rules Service

Manages rate limiting rules and model group configurations.
Allows easy adjustment of rate limits without code changes.

Note: Setting tpm=0 in a model group means no tokens/minute limit
(unlimited tokens). The RPM limit still applies.
"""

import json
from typing import Dict, List, Optional
from dataclasses import dataclass

from src.core.config import settings
from src.core.logging_config import get_core_logger

from .types import RateLimitConfig, ModelGroupConfig

logger = get_core_logger()


# Default model group configurations
# These can be overridden via RATE_LIMIT_MODEL_GROUPS environment variable
DEFAULT_MODEL_GROUPS: List[Dict] = [
    {
        "name": "embedding",
        "rpm": 500,
        "tpm": 0,  # No tokens/minute limit for embeddings
        "models": ["text-embedding-bge-m3"],
        "priority": 10,
        "description": "Embedding models with high throughput (no token limit)"
    },
    {
        "name": "S",
        "rpm": 500,
        "tpm": 1000000,  # 1M tokens/minute
        "models": [
            "llama-3.2-3b",
            "llama-3.2-3b:web",
            "qwen3-4b",
            "qwen3-4b:web",
        ],
        "priority": 25,
        "description": "Small models with high throughput limits"
    },
    {
        "name": "M",
        "rpm": 3,
        "tpm": 750000,  # 750k tokens/minute
        "models": [
            "llama-3.3-70b",
            "llama-3.3-70b:web",
            "mistral-31-24b",
            "mistral-31-24b:web",
            "qwen3-next-80b",
            "qwen3-next-80b:web",
            "venice-uncensored",
            "venice-uncensored:web",
        ],
        "priority": 50,
        "description": "Medium models with moderate limits"
    },
    {
        "name": "L",
        "rpm": 20,
        "tpm": 500000,  # 500k tokens/minute
        "models": [
            "glm-4.6",
            "glm-4.6:web",
            "hermes-3-llama-3.1-405b",
            "hermes-3-llama-3.1-405b:web",
            "kimi-k2-thinking",
            "kimi-k2-thinking:web",
            "qwen3-235b",
            "qwen3-235b:web",
            "qwen3-coder-480b-a35b-instruct",
            "qwen3-coder-480b-a35b-instruct:web",
        ],
        "priority": 100,
        "description": "Large models with conservative limits"
    },
]


class RateLimitRulesService:
    """
    Service for managing rate limiting rules.
    
    Provides:
    - Default rate limits
    - Model group-specific rate limits
    - Dynamic rule configuration via environment variables
    - Model-to-group matching
    """
    
    def __init__(self):
        self._default_config: Optional[RateLimitConfig] = None
        self._model_groups: List[ModelGroupConfig] = []
        self._initialized = False
    
    def initialize(self) -> None:
        """Initialize the rules service with configuration."""
        if self._initialized:
            return
        
        # Load default configuration
        self._default_config = RateLimitConfig(
            rpm=settings.RATE_LIMIT_DEFAULT_RPM,
            tpm=settings.RATE_LIMIT_DEFAULT_TPM,
            window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
        )
        
        # Load model groups
        self._load_model_groups()
        
        self._initialized = True
        logger.info(
            "Rate limit rules initialized",
            default_rpm=self._default_config.rpm,
            default_tpm=self._default_config.tpm,
            model_groups_count=len(self._model_groups),
            event_type="rate_limit_rules_init",
        )
    
    def _load_model_groups(self) -> None:
        """Load model groups from configuration."""
        groups_config = settings.RATE_LIMIT_MODEL_GROUPS
        
        # Try to parse from environment variable first
        if groups_config and groups_config.strip():
            try:
                parsed_groups = json.loads(groups_config)
                if isinstance(parsed_groups, list):
                    self._model_groups = [
                        self._parse_group_config(g) for g in parsed_groups
                    ]
                elif isinstance(parsed_groups, dict):
                    # Support dict format: {"group_name": {...config...}}
                    self._model_groups = [
                        self._parse_group_config({**v, "name": k})
                        for k, v in parsed_groups.items()
                    ]
                
                logger.info(
                    "Loaded model groups from environment",
                    groups_count=len(self._model_groups),
                    event_type="model_groups_loaded",
                )
                return
            except json.JSONDecodeError as e:
                logger.warning(
                    "Failed to parse RATE_LIMIT_MODEL_GROUPS, using defaults",
                    error=str(e),
                    event_type="model_groups_parse_error",
                )
        
        # Fall back to defaults
        self._model_groups = [
            self._parse_group_config(g) for g in DEFAULT_MODEL_GROUPS
        ]
        logger.info(
            "Using default model groups",
            groups_count=len(self._model_groups),
            event_type="model_groups_default",
        )
    
    def _parse_group_config(self, config: Dict) -> ModelGroupConfig:
        """Parse a dictionary into a ModelGroupConfig."""
        return ModelGroupConfig(
            name=config.get("name", "unknown"),
            rpm=config.get("rpm", self._default_config.rpm if self._default_config else 60),
            tpm=config.get("tpm", self._default_config.tpm if self._default_config else 100000),
            models=config.get("models", []),
            priority=config.get("priority", 0),
            description=config.get("description", ""),
        )
    
    @property
    def default_config(self) -> RateLimitConfig:
        """Get the default rate limit configuration."""
        if not self._initialized:
            self.initialize()
        return self._default_config
    
    @property
    def model_groups(self) -> List[ModelGroupConfig]:
        """Get all configured model groups."""
        if not self._initialized:
            self.initialize()
        return self._model_groups
    
    def get_config_for_model(self, model_name: Optional[str]) -> tuple[RateLimitConfig, Optional[str]]:
        """
        Get the rate limit configuration for a specific model.
        
        Args:
            model_name: The name of the model
            
        Returns:
            Tuple of (RateLimitConfig, model_group_name or None)
        """
        if not self._initialized:
            self.initialize()
        
        if not model_name:
            return self._default_config, None
        
        # Sort groups by priority (highest first)
        sorted_groups = sorted(
            self._model_groups,
            key=lambda g: g.priority,
            reverse=True
        )
        
        # Find matching group
        for group in sorted_groups:
            if group.matches_model(model_name):
                config = RateLimitConfig(
                    rpm=group.rpm,
                    tpm=group.tpm,
                    window_seconds=self._default_config.window_seconds,
                )
                return config, group.name
        
        # No match, use default
        return self._default_config, None
    
    def add_model_group(self, group: ModelGroupConfig) -> None:
        """
        Add or update a model group configuration.
        
        Useful for runtime rule updates.
        
        Args:
            group: The model group configuration to add
        """
        if not self._initialized:
            self.initialize()
        
        # Remove existing group with same name
        self._model_groups = [g for g in self._model_groups if g.name != group.name]
        
        # Add new group
        self._model_groups.append(group)
        
        logger.info(
            "Model group added/updated",
            group_name=group.name,
            rpm=group.rpm,
            tpm=group.tpm,
            models_count=len(group.models),
            event_type="model_group_updated",
        )
    
    def remove_model_group(self, group_name: str) -> bool:
        """
        Remove a model group configuration.
        
        Args:
            group_name: The name of the group to remove
            
        Returns:
            True if the group was removed, False if not found
        """
        if not self._initialized:
            self.initialize()
        
        original_count = len(self._model_groups)
        self._model_groups = [g for g in self._model_groups if g.name != group_name]
        
        removed = len(self._model_groups) < original_count
        if removed:
            logger.info(
                "Model group removed",
                group_name=group_name,
                event_type="model_group_removed",
            )
        
        return removed
    
    def get_all_rules_info(self) -> Dict:
        """
        Get a summary of all rate limiting rules.
        
        Useful for admin/debugging endpoints.
        """
        if not self._initialized:
            self.initialize()
        
        return {
            "enabled": settings.RATE_LIMIT_ENABLED,
            "default": {
                "rpm": self._default_config.rpm,
                "tpm": self._default_config.tpm,
                "window_seconds": self._default_config.window_seconds,
            },
            "model_groups": [
                {
                    "name": g.name,
                    "rpm": g.rpm,
                    "tpm": g.tpm,
                    "models": g.models,
                    "priority": g.priority,
                    "description": g.description,
                }
                for g in sorted(self._model_groups, key=lambda x: -x.priority)
            ],
        }
    
    def reload_rules(self) -> None:
        """
        Reload rules from configuration.
        
        Useful for dynamic rule updates without restart.
        """
        self._initialized = False
        self._model_groups = []
        self._default_config = None
        self.initialize()
        logger.info("Rate limit rules reloaded", event_type="rate_limit_rules_reload")


# Singleton instance
rate_limit_rules_service = RateLimitRulesService()

