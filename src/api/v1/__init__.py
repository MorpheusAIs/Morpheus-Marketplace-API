# This file makes api.v1 a Python package 
from src.api.v1.auth import auth_router as auth
from src.api.v1.models import router as models
from src.api.v1.chat import router as chat
from src.api.v1.blockchain import router as blockchain 