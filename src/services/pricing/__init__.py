"""
Usage Pricing Module

Provides pricing estimation and calculation for model usage.
Designed with clean interfaces to support future dynamic pricing from database.

Usage:
    from src.services.pricing import PricingService, get_pricing_service
    
    # Get the singleton pricing service
    service = get_pricing_service()
    
    # Estimate cost before request
    estimate = await service.estimate_usage(model_id="llama-3.3-70b", estimated_input_tokens=1000)
    
    # Calculate final cost after response
    cost = await service.calculate_cost(model_id="llama-3.3-70b", input_tokens=950, output_tokens=500)
"""

from .types import (
    ModelPricing,
    UsageEstimate,
    UsageCost,
)
from .provider import PricingProvider
from .hardcoded_provider import HardcodedPricingProvider
from .pricing_service import PricingService, get_pricing_service

__all__ = [
    # Types
    "ModelPricing",
    "UsageEstimate",
    "UsageCost",
    # Provider interface
    "PricingProvider",
    # Implementations
    "HardcodedPricingProvider",
    # Service
    "PricingService",
    "get_pricing_service",
]

