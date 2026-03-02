# This file makes api.v1 a Python package 
from fastapi import APIRouter

from .auth.index import router as auth_router
from .models.index import router as models_router
from .chat.index import router as chat_router
from .chat_history.index import router as chat_history_router
from .embeddings.index import router as embeddings_router
from .audio.index import router as audio_router
from .billing.index import router as billing_router
from .webhooks.stripe import stripe_webhook_router
from .webhooks.coinbase import coinbase_webhook_router
from .wallet.index import router as wallet_router

# Create routers with the fixed dependency route class
models = APIRouter()
models.include_router(models_router)

chat = APIRouter()
chat.include_router(chat_router)

embeddings = APIRouter()
embeddings.include_router(embeddings_router)

audio = APIRouter()
audio.include_router(audio_router)

# Wrap auth router in a router with our fixed route class
auth = APIRouter()
auth.include_router(auth_router)

# Chat history router
chat_history = APIRouter()
chat_history.include_router(chat_history_router)

# Billing router
billing = APIRouter()
billing.include_router(billing_router)

# Webhooks router
webhooks = APIRouter()
webhooks.include_router(stripe_webhook_router)
webhooks.include_router(coinbase_webhook_router)

# Wallet router
wallet = APIRouter()
wallet.include_router(wallet_router)
