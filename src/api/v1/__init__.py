# This file makes api.v1 a Python package 
from fastapi import APIRouter

from .auth.index import router as auth_router
from .models.index import router as models_router
from .chat.index import router as chat_router
from .chat.index import ChatMessage, ToolFunction, Tool, ToolChoice, ChatCompletionRequest
from .session.index import router as session_router
from .automation.index import router as automation_router
from .chat_history.index import router as chat_history_router
from .embeddings.index import router as embeddings_router

# Create routers with the fixed dependency route class
models = APIRouter()
models.include_router(models_router)

chat = APIRouter()
chat.include_router(chat_router)

session = APIRouter()
session.include_router(session_router)

# Automation router
automation = APIRouter()
automation.include_router(automation_router)

# Wrap auth router in a router with our fixed route class
auth = APIRouter()
auth.include_router(auth_router)

# Chat history router
chat_history = APIRouter()
chat_history.include_router(chat_history_router)

# Embeddings router
embeddings = APIRouter()
embeddings.include_router(embeddings_router) 