from typing import Dict, Optional, Set, Tuple

from .direct_model_service import direct_model_service
from .config import settings
from .logging_config import get_models_logger
from .model_errors import (
    ModelDeniedError,
    ModelNearMissError,
    ModelNotAllowlistedError,
    ModelTypeMismatchError,
)
from .session_routing_policy import get_session_routing_policy

# Configure logger
logger = get_models_logger()

# Get default model from settings
DEFAULT_MODEL = getattr(settings, 'DEFAULT_FALLBACK_MODEL', "mistral-31-24b")
DEFAULT_EMBEDDINGS_MODEL = getattr(settings, 'DEFAULT_FALLBACK_EMBEDDINGS_MODEL', "text-embedding-bge-m3")
DEFAULT_TTS_MODEL = getattr(settings, 'DEFAULT_FALLBACK_TTS_MODEL', "tts-kokoro")
DEFAULT_STT_MODEL = getattr(settings, 'DEFAULT_FALLBACK_STT_MODEL', "whisper-1")


def parse_model_id_csv(raw: Optional[str]) -> Set[str]:
    """Parse a comma-separated list of model blockchain IDs."""
    if not raw:
        return set()
    return {m.strip() for m in raw.split(",") if m.strip()}


def parse_model_aliases(raw: Optional[str]) -> Dict[str, str]:
    """Parse SESSION_MODEL_ALIASES into lowercased alias → target map.

    Accepts ``alias=target``, ``alias->target``, or ``alias→target`` pairs,
    comma-separated. Values keep their configured casing (names or 0x IDs).
    """
    if not raw:
        return {}
    out: Dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        key: Optional[str] = None
        value: Optional[str] = None
        for sep in ("→", "->", "="):
            if sep in part:
                left, right = part.split(sep, 1)
                key, value = left.strip(), right.strip()
                break
        if not key or not value:
            continue
        out[key.lower()] = value
    return out


def _effective_aliases() -> Dict[str, str]:
    """Prefer JSON policy aliases; fall back to legacy CSV SESSION_MODEL_ALIASES."""
    policy = get_session_routing_policy()
    if policy.aliases:
        return dict(policy.aliases)
    return parse_model_aliases(getattr(settings, "SESSION_MODEL_ALIASES", "") or "")


def apply_model_alias(
    requested_model: str,
    aliases: Optional[Dict[str, str]] = None,
) -> Tuple[str, Optional[str]]:
    """Return (possibly rewritten name, matched alias key or None)."""
    if aliases is None:
        aliases = _effective_aliases()
    if not aliases or not requested_model:
        return requested_model, None
    hit = aliases.get(requested_model.lower())
    if hit:
        return hit, requested_model.lower()
    return requested_model, None


class ModelRouter:
    """
    Handles routing of model names to blockchain IDs using DirectModelService.
    """

    # Acceptable active_models.json ModelType values per request type. Request
    # types not listed here (e.g. TTS/STT, which the feed does not type
    # distinctly yet) skip the compatibility check.
    COMPATIBLE_MODEL_TYPES = {
        "LLM": {"LLM"},
        "EMBEDDINGS": {"EMBEDDING"},
    }

    def __init__(self):
        logger.info("Initialized ModelRouter with DirectModelService",
                   event_type="model_router_init")
        # No initialization needed - DirectModelService handles all caching

    def _allowed_model_ids(self) -> Set[str]:
        return parse_model_id_csv(getattr(settings, "SESSION_ALLOWED_MODEL_IDS", "") or "")

    def _ensure_allowlisted(self, requested_model: Optional[str], resolved_id: str) -> str:
        """Raise ModelNotAllowlistedError when curated allowlist is active."""
        allowed = self._allowed_model_ids()
        if not allowed:
            return resolved_id
        if resolved_id in allowed:
            return resolved_id
        logger.warning(
            "Resolved model is outside hosted-gateway allowlist",
            requested_model=requested_model,
            resolved_id=resolved_id,
            event_type="model_not_allowlisted",
        )
        raise ModelNotAllowlistedError(
            requested_model=requested_model,
            resolved_id=resolved_id,
        )

    async def _ensure_not_denied(
        self,
        requested_model: Optional[str],
        resolved_id: str,
    ) -> str:
        """Raise ModelDeniedError when resolved identity is on the deny list."""
        policy = get_session_routing_policy()
        native_name = await direct_model_service.get_model_name_from_id(resolved_id)
        if (
            policy.is_denied_id(resolved_id)
            or policy.is_denied_name(requested_model)
            or policy.is_denied_name(native_name)
        ):
            logger.warning(
                "Resolved model is on hosted-gateway deny list",
                requested_model=requested_model,
                resolved_id=resolved_id,
                resolved_model=native_name,
                event_type="model_denied",
            )
            raise ModelDeniedError(
                requested_model=requested_model,
                resolved_id=resolved_id,
                resolved_model=native_name,
            )
        return resolved_id

    async def _resolve_preference_or_name(self, label: str) -> Optional[str]:
        """Resolve a preference/alias target that may already be a 0x ID."""
        if not label:
            return None
        if label.startswith("0x") and len(label) >= 10:
            # Prefer exact blockchain id when configured.
            ids = await direct_model_service.get_blockchain_ids()
            if label in ids:
                return label
        return await direct_model_service.resolve_model_id(label)

    async def get_target_model(self, requested_model: Optional[str], type: Optional[str] = "LLM") -> str:
        """
        Get the target blockchain ID for the requested model.
        
        Args:
            requested_model: The model name or blockchain ID requested by the user
            
        Returns:
            str: The blockchain ID to use

        Raises:
            ModelTypeMismatchError: Model exists but is wrong type for the endpoint
            ModelNearMissError: Name not found, but close catalog matches exist
            ModelNotAllowlistedError: Resolved ID outside SESSION_ALLOWED_MODEL_IDS
        """
        # return "0xe086adc275c99e32bb10b0aff5e8bfc391aad18cbb184727a75b2569149425c6"
        logger.info("Getting target model for requested model",
                   requested_model=requested_model,
                   event_type="model_resolution_start",
                   model_type=type)
        
        if not requested_model:
            logger.warning("No model specified, using default model",
                          default_model=DEFAULT_MODEL,
                          event_type="default_model_fallback")
            default_id = await self._get_default_model_id(type)
            logger.info("Resolved to default model ID",
                       default_model_id=default_id,
                       event_type="default_model_resolved")
            return self._ensure_allowlisted(DEFAULT_MODEL, default_id)

        aliased_model, alias_key = apply_model_alias(requested_model)
        if alias_key:
            logger.info(
                "Applied model family alias",
                requested_model=requested_model,
                alias_key=alias_key,
                aliased_model=aliased_model,
                event_type="model_alias_applied",
            )

        policy = get_session_routing_policy()
        preference_target = (
            policy.preference_target(requested_model)
            or policy.preference_target(aliased_model)
        )
        if preference_target:
            logger.info(
                "Applied model preference",
                requested_model=requested_model,
                preference_target=preference_target,
                event_type="model_preference_applied",
            )

        # Try to resolve using DirectModelService (preferences win on collisions).
        try:
            if preference_target:
                resolved_id = await self._resolve_preference_or_name(preference_target)
            elif aliased_model.startswith("0x"):
                resolved_id = await self._resolve_preference_or_name(aliased_model)
            else:
                resolved_id = await direct_model_service.resolve_model_id(aliased_model)

            # A resolved model must be usable by this endpoint type. Without
            # this check a chat completion naming an EMBEDDING model opens a
            # session with the embedding provider, whose backend then rejects
            # the chat payload. Hard-fail so agents don't silently get Gemma.
            if resolved_id and not await self._is_type_compatible(resolved_id, type):
                model_name = await direct_model_service.get_model_name_from_id(resolved_id)
                model_types = await direct_model_service.get_model_mapping_type()
                actual_type = (
                    model_types.get(model_name.lower()) if model_name else None
                )
                logger.warning("Requested model type is incompatible with endpoint",
                              requested_model=requested_model,
                              resolved_id=resolved_id,
                              resolved_model=model_name,
                              requested_type=type,
                              model_type=actual_type,
                              event_type="model_type_mismatch")
                raise ModelTypeMismatchError(
                    requested_model=requested_model,
                    resolved_model=model_name,
                    resolved_id=resolved_id,
                    requested_type=type,
                    model_type=actual_type,
                )

            if resolved_id:
                logger.info("Found model mapping",
                           requested_model=requested_model,
                           aliased_model=aliased_model if alias_key else None,
                           preference_target=preference_target,
                           resolved_id=resolved_id,
                           event_type="model_resolved")
                await self._ensure_not_denied(requested_model, resolved_id)
                return self._ensure_allowlisted(requested_model, resolved_id)

            # Not found — if we have close matches, hard-fail with suggestions
            # so agents stop / alert instead of continuing on the default model.
            # True unknowns (no near miss) still soft-fallback for operability
            # unless a curated allowlist is active (then 503, no silent rewrite).
            logger.warning("Model not found in active models",
                          requested_model=requested_model,
                          aliased_model=aliased_model if alias_key else None,
                          event_type="model_not_found")
            suggestions = await direct_model_service.suggest_models(aliased_model)
            if suggestions:
                logger.warning("Near-miss model name; returning suggestions",
                              requested_model=requested_model,
                              suggestions=suggestions,
                              event_type="model_not_found_near_miss")
                raise ModelNearMissError(
                    requested_model=requested_model,
                    suggestions=suggestions,
                )

            if self._allowed_model_ids():
                raise ModelNotAllowlistedError(
                    requested_model=requested_model,
                    resolved_id=None,
                )

            model_mapping = await direct_model_service.get_model_mapping()
            blockchain_ids = await direct_model_service.get_blockchain_ids()
            logger.info("Available models for debugging",
                       available_models=sorted(list(model_mapping.keys())),
                       available_blockchain_ids=sorted(list(blockchain_ids)),
                       requested_model=requested_model)

            default_id = await self._get_default_model_id(type)
            logger.warning("Using default model fallback",
                          requested_model=requested_model,
                          default_model_id=default_id,
                          event_type="default_model_fallback")
            return default_id
        except (
            ModelTypeMismatchError,
            ModelNearMissError,
            ModelNotAllowlistedError,
            ModelDeniedError,
        ):
            raise
        except Exception as e:
            logger.error("Error resolving model - using default fallback",
                        requested_model=requested_model,
                        error=str(e),
                        event_type="model_resolution_error")
            if self._allowed_model_ids():
                raise ModelNotAllowlistedError(
                    requested_model=requested_model,
                    resolved_id=None,
                ) from e
            # Fall back to default model
            default_id = await self._get_default_model_id(type)
            logger.warning("Using default model ID due to error",
                          requested_model=requested_model,
                          default_model_id=default_id,
                          event_type="default_model_error_fallback")
            return default_id
    
    async def _is_type_compatible(self, blockchain_id: str, type: Optional[str]) -> bool:
        """
        Check whether a resolved model's ModelType is usable for the requested
        endpoint type ("LLM", "EMBEDDINGS", ...).

        Returns True when the request type has no compatibility rule, or the
        model's type cannot be determined (fail open - never block routing on
        missing metadata).
        """
        compatible_types = self.COMPATIBLE_MODEL_TYPES.get(type)
        if not compatible_types:
            return True

        try:
            model_name = await direct_model_service.get_model_name_from_id(blockchain_id)
            if not model_name:
                return True
            model_types = await direct_model_service.get_model_mapping_type()
            model_type = model_types.get(model_name.lower())
            if not model_type:
                return True
            return model_type in compatible_types
        except Exception as e:
            logger.error("Error checking model type compatibility - failing open",
                        blockchain_id=blockchain_id,
                        requested_type=type,
                        error=str(e),
                        event_type="model_type_check_error")
            return True

    async def _get_default_model_id(self, type: Optional[str] = "LLM") -> str:
        """Get the blockchain ID for the default model"""
        try:
            model_mapping = await direct_model_service.get_model_mapping()
            model_mapping_type = await direct_model_service.get_model_mapping_type()

            match type:
                case "LLM":
                    default_model = DEFAULT_MODEL
                case "EMBEDDINGS":
                    default_model = DEFAULT_EMBEDDINGS_MODEL
                case "TTS":
                    default_model = DEFAULT_TTS_MODEL
                case "STT":
                    default_model = DEFAULT_STT_MODEL
                case _:
                    default_model = DEFAULT_MODEL
            
            # First try the explicitly defined default
            if default_model in model_mapping:
                logger.info("Using configured default model",
                           default_model=default_model,
                           blockchain_id=model_mapping[default_model.lower()],
                           event_type="default_model_resolved")
                return model_mapping[default_model]
                
            # If no default model is found, use the first available model
            if model_mapping and model_mapping_type:
                model_name = None
                for model_name, model_type in model_mapping_type.items():
                    if model_type == type:
                        model_name = model_name
                        break
                
                if model_name:
                    logger.warning("No default model configured, using first available model",
                              first_model_name=model_name,
                              first_model_id=model_mapping[model_name],
                              event_type="first_available_model_fallback")
                    return model_mapping[model_name]
                else:
                    raise ValueError("No default model configured, no model of type found")
                
            # If there are no models at all, raise an error
            logger.error("No models available in the system, cannot route",
                       event_type="no_models_available_error")
            raise ValueError("No models available in the system")
        except Exception as e:
            logger.error("Error getting default model",
                        error=str(e),
                        event_type="default_model_fetch_error")
            raise ValueError(f"Error getting default model: {e}")
    
    async def get_model_name_from_id(self, blockchain_id: str) -> Optional[str]:
        """
        Get the human-readable model name for a blockchain ID.
        
        Args:
            blockchain_id: The blockchain ID to look up
            
        Returns:
            str: The model name, or None if not found
        """
        try:
            return await direct_model_service.get_model_name_from_id(blockchain_id)
        except Exception as e:
            logger.error("Error resolving model name from ID",
                        blockchain_id=blockchain_id,
                        error=str(e),
                        event_type="model_name_resolution_error")
            return None
    
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
