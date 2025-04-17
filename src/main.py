from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import time
import logging
import asyncio

from src.api.v1.auth import auth_router
from src.core.config import settings
from src.api.v1 import models, chat
from src.services.init_cache import test_redis_connection, init_model_cache

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Morpheus API Gateway",
    description="API Gateway connecting Web2 clients to the Morpheus-Lumerin AI Marketplace",
    version="0.1.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Set up CORS
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response

# Error handler for OpenAI-compatible error responses
@app.exception_handler(Exception)
async def openai_exception_handler(request: Request, exc: Exception):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    if hasattr(exc, "status_code"):
        status_code = exc.status_code
    
    # Format error response in OpenAI style
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": str(exc),
                "type": exc.__class__.__name__,
                "param": None,
                "code": None
            }
        }
    )

# Application startup event
@app.on_event("startup")
async def startup_event():
    # Test Redis connection
    redis_ok = test_redis_connection()
    if not redis_ok:
        logger.warning("Redis connection failed. Cache features may not work correctly.")
    
    # Initialize model cache
    await init_model_cache()
    logger.info("Application startup complete")

# Include routers
app.include_router(auth_router, prefix=f"{settings.API_V1_STR}/auth", tags=["Authentication"])
app.include_router(models.router, prefix=f"{settings.API_V1_STR}/models", tags=["Models"])
app.include_router(chat.router, prefix=f"{settings.API_V1_STR}/chat", tags=["Chat"])

@app.get("/")
async def root():
    return {
        "name": settings.PROJECT_NAME,
        "version": "0.1.0",
        "description": "OpenAI-compatible API gateway for Morpheus blockchain models"
    }

# Health check endpoint with Redis status
@app.get("/health")
async def health_check():
    # Check Redis connection
    redis_status = "ok" if test_redis_connection() else "unavailable"
    
    return {
        "status": "ok",
        "redis": redis_status
    }

# TODO: Add other routers once implemented
# from src.api.v1.models import models_router
# from src.api.v1.chat import chat_router
# app.include_router(models_router, prefix=f"{settings.API_V1_STR}", tags=["Models"])
# app.include_router(chat_router, prefix=f"{settings.API_V1_STR}", tags=["Chat"]) 