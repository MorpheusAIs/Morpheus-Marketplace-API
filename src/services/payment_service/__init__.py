"""
Payment Service Module

This module provides payment processing services for:
- Stripe credit card payments
- Coinbase Commerce cryptocurrency payments
- MOR token payments

TODO: Implement full payment integration
"""

from .stripe_service import StripeService
from .coinbase_service import CoinbaseService
from .mor_payment_service import MORPaymentService

# Initialize service instances
stripe_service = StripeService()
coinbase_service = CoinbaseService()
mor_payment_service = MORPaymentService()

__all__ = [
    "stripe_service",
    "coinbase_service",
    "mor_payment_service",
    "StripeService",
    "CoinbaseService",
    "MORPaymentService",
]
