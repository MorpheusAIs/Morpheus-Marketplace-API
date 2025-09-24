# This file makes api.v1 a Python package 
from fastapi import APIRouter

from .auth import router as auth_router
from .models import router as models_router
from .chat import router as chat_router
from .session import router as session_router
from .automation import router as automation_router
from .chat_history import router as chat_history_router

# Create routers with standard APIRoute (no custom route class needed)
models = APIRouter()
models.include_router(models_router)

chat = APIRouter()
chat.include_router(chat_router)

session = APIRouter()
session.include_router(session_router)

# Automation router
automation = APIRouter()
automation.include_router(automation_router)

# Auth router
auth = APIRouter()
auth.include_router(auth_router)

# Chat history router
chat_history = APIRouter()
chat_history.include_router(chat_history_router) 