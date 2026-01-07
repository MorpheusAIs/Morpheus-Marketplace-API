"""
Webhooks API module for handling external service callbacks.
"""
from .stripe import stripe_webhook_router

__all__ = ["stripe_webhook_router"]

