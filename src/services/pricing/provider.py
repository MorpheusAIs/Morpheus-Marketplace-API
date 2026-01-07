"""
Abstract interface for pricing providers.

This module defines the contract that all pricing providers must implement.
This allows for easy swapping between hardcoded, database-backed, or external pricing sources.
"""

from abc import ABC, abstractmethod
from typing import Optional, List, TYPE_CHECKING, Any
from decimal import Decimal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from .types import ModelPricing


class PricingProvider(ABC):
    """
    Abstract base class for pricing data providers.
    
    Implementations can be:
    - HardcodedPricingProvider: Static pricing defined in code
    - DatabasePricingProvider: Dynamic pricing from database tables (future)
    - ExternalPricingProvider: Pricing from external API (future)
    - CachedPricingProvider: Wrapper that caches pricing data (future)
    
    All implementations should be async-compatible even if they don't need it,
    to maintain consistent interface for future database-backed implementations.
    """
    
    @property
    @abstractmethod
    def source_name(self) -> str:
        """
        Return the name of this pricing source.
        Used for logging and audit purposes.
        
        Returns:
            str: Source identifier (e.g., "hardcoded", "database", "external_api")
        """
        pass
    
    @abstractmethod
    async def get_model_pricing(
        self,
        model_name: Optional[str] = None,
        model_id: Optional[str] = None,
        db: Optional["AsyncSession"] = None,
    ) -> Optional[ModelPricing]:
        """
        Get pricing configuration for a specific model.
        
        At least one of model_name or model_id must be provided.
        If both are provided, implementations may use either for lookup.
        
        Args:
            model_name: Human-readable model name (e.g., "llama-3.3-70b")
            model_id: Hex32 blockchain model identifier
            db: Optional database session (needed for DB-backed providers)
            
        Returns:
            ModelPricing if found, None otherwise
        """
        pass
    
    @abstractmethod
    async def get_all_model_pricing(
        self,
        db: Optional["AsyncSession"] = None,
    ) -> List[ModelPricing]:
        """
        Get pricing for all available models.
        
        Args:
            tier: Pricing tier filter
            db: Optional database session
            
        Returns:
            List of ModelPricing for all models
        """
        pass
    
    @abstractmethod
    async def get_default_pricing(self) -> ModelPricing:
        """
        Get default/fallback pricing when model-specific pricing is not available.
        
        Returns:
            ModelPricing with default values
        """
        pass
    
    async def supports_model(
        self,
        model_name: Optional[str] = None,
        model_id: Optional[str] = None,
        db: Optional["AsyncSession"] = None,
    ) -> bool:
        """
        Check if pricing is available for a model.
        
        Default implementation checks if get_model_pricing returns a value.
        Can be overridden for more efficient implementations.
        
        Args:
            model_name: Human-readable model name
            model_id: Hex32 blockchain model identifier
            db: Optional database session
            
        Returns:
            True if pricing is available
        """
        pricing = await self.get_model_pricing(model_name=model_name, model_id=model_id, db=db)
        return pricing is not None
    
    async def get_pricing_or_default(
        self,
        model_name: Optional[str] = None,
        model_id: Optional[str] = None,
        db: Optional["AsyncSession"] = None,
    ) -> ModelPricing:
        """
        Get pricing for a model, falling back to default if not found.
        
        Args:
            model_name: Human-readable model name
            model_id: Hex32 blockchain model identifier
            db: Optional database session
            
        Returns:
            ModelPricing for the model or default pricing
        """
        pricing = await self.get_model_pricing(model_name=model_name, model_id=model_id, db=db)
        if pricing is not None:
            return pricing
        return await self.get_default_pricing()

