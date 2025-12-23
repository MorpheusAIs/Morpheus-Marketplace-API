"""
Main pricing service for usage cost estimation and calculation.

This service provides the public interface for all pricing operations.
It uses a PricingProvider internally, which can be swapped for different
implementations (hardcoded, database-backed, external API).
"""

from typing import Optional, TYPE_CHECKING
from decimal import Decimal
from datetime import datetime
import logging

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from .types import ModelPricing, UsageEstimate, UsageCost
from .provider import PricingProvider
from .hardcoded_provider import HardcodedPricingProvider

# Try to import the project logger, fall back to standard logging
try:
    from src.core.logging_config import get_core_logger
    logger = get_core_logger()
except ImportError:
    logger = logging.getLogger(__name__)

# Default estimated output tokens when not provided
DEFAULT_ESTIMATED_OUTPUT_TOKENS = 500


class PricingService:
    """
    Service for estimating and calculating usage costs.
    
    Provides two main functions:
    1. estimate_usage() - Estimate cost before making a request
    2. calculate_cost() - Calculate actual cost after response
    
    The service is provider-agnostic and can work with any PricingProvider
    implementation. This allows easy transition from hardcoded to dynamic
    pricing without changing the consuming code.
    
    Usage:
        service = PricingService()  # Uses default hardcoded provider
        
        # Or with custom provider:
        db_provider = DatabasePricingProvider()
        service = PricingService(provider=db_provider)
    """
    
    def __init__(self, provider: Optional[PricingProvider] = None):
        """
        Initialize the pricing service.
        
        Args:
            provider: Pricing provider to use. Defaults to HardcodedPricingProvider.
        """
        self._provider = provider or HardcodedPricingProvider()
    
    @property
    def provider(self) -> PricingProvider:
        """Get the current pricing provider."""
        return self._provider
    
    def set_provider(self, provider: PricingProvider) -> None:
        """
        Set a new pricing provider.
        
        Useful for switching between providers at runtime or in tests.
        
        Args:
            provider: New pricing provider to use
        """
        logger.info(
            "Switching pricing provider",
            old_provider=self._provider.source_name,
            new_provider=provider.source_name,
        )
        self._provider = provider
    
    async def estimate_usage(
        self,
        estimated_input_tokens: int,
        estimated_output_tokens: Optional[int] = None,
        model_name: Optional[str] = None,
        model_id: Optional[str] = None,
        db: Optional["AsyncSession"] = None,
    ) -> UsageEstimate:
        """
        Estimate usage cost before making a request.
        
        This is used to:
        - Check if user has sufficient credits before processing
        - Create holds on user balance
        - Display estimated costs to users
        
        Args:
            estimated_input_tokens: Estimated number of input tokens
            estimated_output_tokens: Estimated output tokens (defaults to 500)
            model_name: Human-readable model name (e.g., "llama-3.3-70b")
            model_id: Hex32 blockchain model identifier
            db: Optional database session for DB-backed providers
            
        Returns:
            UsageEstimate with estimated costs
            
        Example:
            estimate = await service.estimate_usage(
                model_name="llama-3.3-70b",
                estimated_input_tokens=1000,
                estimated_output_tokens=500,
            )
            print(f"Estimated cost: ${estimate.estimated_total_cost}")
        """
        output_tokens = estimated_output_tokens or DEFAULT_ESTIMATED_OUTPUT_TOKENS
        
        # Get pricing for the model
        pricing = await self._provider.get_pricing_or_default(
            model_name=model_name, model_id=model_id, db=db
        )
        
        # Calculate costs
        input_cost = pricing.calculate_input_cost(estimated_input_tokens)
        output_cost = pricing.calculate_output_cost(output_tokens)
        total_cost = input_cost + output_cost
        
        # Use provided values or fall back to pricing values
        result_model_name = model_name or pricing.model_name
        result_model_id = model_id or pricing.model_id
        
        logger.debug(
            "Usage estimated",
            model_name=result_model_name,
            model_id=result_model_id,
            input_tokens=estimated_input_tokens,
            output_tokens=output_tokens,
            input_cost=str(input_cost),
            output_cost=str(output_cost),
            total_cost=str(total_cost),
            pricing_source=self._provider.source_name,
        )
        
        return UsageEstimate(
            model_name=result_model_name,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=output_tokens,
            estimated_input_cost=input_cost,
            estimated_output_cost=output_cost,
            estimated_total_cost=total_cost,
            model_id=result_model_id,
            currency=pricing.currency,
            confidence=1.0 if pricing.model_name != "default" else 0.5,
            pricing_source=self._provider.source_name,
        )
    
    async def calculate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        model_name: Optional[str] = None,
        model_id: Optional[str] = None,
        db: Optional["AsyncSession"] = None,
    ) -> UsageCost:
        """
        Calculate actual usage cost after request completion.
        
        This is used to:
        - Finalize holds with actual usage
        - Create accurate billing records
        - Track actual spending
        
        Args:
            input_tokens: Actual input token count from response
            output_tokens: Actual output token count from response
            model_name: Human-readable model name (e.g., "llama-3.3-70b")
            model_id: Hex32 blockchain model identifier
            db: Optional database session for DB-backed providers
            
        Returns:
            UsageCost with actual costs
            
        Example:
            cost = await service.calculate_cost(
                model_name="llama-3.3-70b",
                input_tokens=950,
                output_tokens=487,
            )
            print(f"Actual cost: ${cost.total_cost}")
        """
        # Get pricing for the model
        pricing = await self._provider.get_pricing_or_default(
            model_name=model_name, model_id=model_id, db=db
        )
        
        # Calculate costs
        input_cost = pricing.calculate_input_cost(input_tokens)
        output_cost = pricing.calculate_output_cost(output_tokens)
        total_cost = input_cost + output_cost
        
        # Use provided values or fall back to pricing values
        result_model_name = model_name or pricing.model_name
        result_model_id = model_id or pricing.model_id
        
        logger.debug(
            "Usage cost calculated",
            model_name=result_model_name,
            model_id=result_model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost=str(input_cost),
            output_cost=str(output_cost),
            total_cost=str(total_cost),
            pricing_source=self._provider.source_name,
        )
        
        return UsageCost(
            model_name=result_model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=total_cost,
            model_id=result_model_id,
            currency=pricing.currency,
            pricing_source=self._provider.source_name,
            calculated_at=datetime.utcnow(),
        )
    
    async def get_model_pricing(
        self,
        model_name: Optional[str] = None,
        model_id: Optional[str] = None,
        db: Optional["AsyncSession"] = None,
    ) -> Optional[ModelPricing]:
        """
        Get pricing configuration for a specific model.
        
        Args:
            model_name: Human-readable model name
            model_id: Hex32 blockchain model identifier
            db: Optional database session
            
        Returns:
            ModelPricing if available, None otherwise
        """
        return await self._provider.get_model_pricing(
            model_name=model_name, model_id=model_id, db=db
        )
    
    async def is_model_supported(
        self,
        model_name: Optional[str] = None,
        model_id: Optional[str] = None,
        db: Optional["AsyncSession"] = None,
    ) -> bool:
        """
        Check if pricing is available for a model.
        
        Args:
            model_name: Human-readable model name
            model_id: Hex32 blockchain model identifier
            db: Optional database session
            
        Returns:
            True if explicit pricing exists (not using default)
        """
        return await self._provider.supports_model(
            model_name=model_name, model_id=model_id, db=db
        )


# Singleton instance for convenience
_pricing_service: Optional[PricingService] = None


def get_pricing_service() -> PricingService:
    """
    Get the global pricing service instance.
    
    Returns a singleton instance of PricingService with the default
    (hardcoded) provider. Use this for simple access without dependency
    injection.
    
    For testing or custom providers, create a new PricingService instance
    directly or use set_pricing_service().
    
    Returns:
        PricingService singleton
    """
    global _pricing_service
    if _pricing_service is None:
        _pricing_service = PricingService()
    return _pricing_service


def set_pricing_service(service: PricingService) -> None:
    """
    Set the global pricing service instance.
    
    Useful for testing or switching to a different provider globally.
    
    Args:
        service: PricingService instance to use globally
    """
    global _pricing_service
    _pricing_service = service

