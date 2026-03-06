"""
Rate Limit Rules Service

Manages rate limiting rules and model group configurations.
Loads configuration from environment-specific JSON files in the models/ directory.

Note: Setting tpm=0 in a model group means no tokens/minute limit
(unlimited tokens). The RPM limit still applies.
"""

from typing import Dict, List, Optional

from src.core.config import settings
from src.core.config_loader import load_rate_limits
from src.core.logging_config import get_core_logger

from .types import RateLimitConfig, ModelGroupConfig

logger = get_core_logger()


class RateLimitRulesService:
    """
    Service for managing rate limiting rules.

    Loads default limits and model-group overrides from
    models/{env}_rate_limit.json, selected by the ENVIRONMENT variable.
    """

    def __init__(self):
        self._default_config: Optional[RateLimitConfig] = None
        self._model_groups: List[ModelGroupConfig] = []
        self._initialized = False

    def initialize(self) -> None:
        """Initialize the rules service from JSON config."""
        if self._initialized:
            return

        config = load_rate_limits()

        self._default_config = RateLimitConfig(
            rpm=config.get("default_rpm", 60),
            tpm=config.get("default_tpm", 100000),
            window_seconds=config.get("window_seconds", 60),
        )

        self._model_groups = [
            self._parse_group_config(g)
            for g in config.get("model_groups", [])
        ]

        self._initialized = True
        logger.info(
            "Rate limit rules initialized",
            default_rpm=self._default_config.rpm,
            default_tpm=self._default_config.tpm,
            model_groups_count=len(self._model_groups),
            event_type="rate_limit_rules_init",
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

        sorted_groups = sorted(
            self._model_groups,
            key=lambda g: g.priority,
            reverse=True
        )

        for group in sorted_groups:
            if group.matches_model(model_name):
                config = RateLimitConfig(
                    rpm=group.rpm,
                    tpm=group.tpm,
                    window_seconds=self._default_config.window_seconds,
                )
                return config, group.name

        return self._default_config, None

    def add_model_group(self, group: ModelGroupConfig) -> None:
        """
        Add or update a model group configuration.

        Useful for runtime rule updates.
        """
        if not self._initialized:
            self.initialize()

        self._model_groups = [g for g in self._model_groups if g.name != group.name]
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
        """Get a summary of all rate limiting rules."""
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
        Reload rules from JSON config.

        Clears the config_loader cache so the file is re-read from disk.
        """
        from src.core.config_loader import clear_cache
        clear_cache()
        self._initialized = False
        self._model_groups = []
        self._default_config = None
        self.initialize()
        logger.info("Rate limit rules reloaded", event_type="rate_limit_rules_reload")


# Singleton instance
rate_limit_rules_service = RateLimitRulesService()
