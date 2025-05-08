# This file makes api.v1 a Python package 
from fastapi import APIRouter

from .custom_route import FixedDependencyAPIRoute
from .auth import router as auth_router
from .models import router as models_router
from .chat import router as chat_router
from .session import router as session_router
from .automation import router as automation_router

# Create routers with the fixed dependency route class
models = APIRouter(route_class=FixedDependencyAPIRoute)
models.include_router(models_router)

chat = APIRouter(route_class=FixedDependencyAPIRoute)
chat.include_router(chat_router)

session = APIRouter(route_class=FixedDependencyAPIRoute)
session.include_router(session_router)

# Automation router
automation = APIRouter(route_class=FixedDependencyAPIRoute)
automation.include_router(automation_router)

# Wrap auth router in a router with our fixed route class
auth = APIRouter(route_class=FixedDependencyAPIRoute)
auth.include_router(auth_router) 