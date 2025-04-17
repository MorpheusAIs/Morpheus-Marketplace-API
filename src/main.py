from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles
import time
import logging
import asyncio

from src.api.v1.auth import auth_router
from src.core.config import settings
from src.api.v1 import models, chat, blockchain
from src.services.init_cache import test_redis_connection, init_model_cache

# Add the import for testing database connection
from sqlalchemy.ext.asyncio import AsyncEngine
from src.db.database import engine

# Import what we need for proper SQL execution
from sqlalchemy import text

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
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    swagger_ui_parameters={
        "persistAuthorization": True,
        "defaultModelsExpandDepth": -1,
        "displayRequestDuration": True,
        "deepLinking": True,
        "tryItOutEnabled": True,
        "docExpansion": "list"
    }
)

# Set up CORS
if hasattr(settings, 'BACKEND_CORS_ORIGINS'):
    origins = []
    if isinstance(settings.BACKEND_CORS_ORIGINS, list):
        origins = settings.BACKEND_CORS_ORIGINS
    elif isinstance(settings.BACKEND_CORS_ORIGINS, str):
        origins = [settings.BACKEND_CORS_ORIGINS]
    
    if origins and origins[0] == "*":
        # Allow all origins
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        # Use specified origins
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
else:
    # If no CORS origins specified, allow all origins (for development)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
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

# Custom docs endpoint
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - API Documentation",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@4/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@4/swagger-ui.css",
        swagger_ui_parameters={
            "persistAuthorization": True,
            "defaultModelsExpandDepth": -1,
            "displayRequestDuration": True,
            "deepLinking": True,
            "docExpansion": "list",
            "filter": True,
            "tryItOutEnabled": True,
            "syntaxHighlight.theme": "monokai",
            "dom_id": "#swagger-ui",
            "layout": "BaseLayout",
            "onComplete": """
                function() {
                    // Add helpful instruction panel
                    const instructionDiv = document.createElement('div');
                    instructionDiv.innerHTML = `
                        <div style="background-color: #f0f8ff; padding: 15px; margin-bottom: 20px; border-radius: 5px; border-left: 5px solid #007bff;">
                            <h3 style="margin-top: 0;">Authentication Guide</h3>
                            <p><strong>Step 1:</strong> Register or Login using the /auth/register or /auth/login endpoints</p>
                            <p><strong>Step 2:</strong> Copy the access_token from the response</p>
                            <p><strong>Step 3:</strong> Click the "Authorize" button and paste the token in the BearerAuth field (without "Bearer" prefix)</p>
                            <p>After authorizing, you can access protected endpoints.</p>
                        </div>
                    `;
                    
                    const swaggerUI = document.querySelector('.swagger-ui');
                    const infoContainer = swaggerUI.querySelector('.information-container');
                    infoContainer.after(instructionDiv);
                }
            """
        }
    )

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
app.include_router(models, prefix=f"{settings.API_V1_STR}/models", tags=["Models"])
app.include_router(chat, prefix=f"{settings.API_V1_STR}/chat", tags=["Chat"])
app.include_router(blockchain, prefix=f"{settings.API_V1_STR}/blockchain", tags=["Blockchain"])

@app.get("/")
async def root():
    return {
        "name": settings.PROJECT_NAME,
        "version": "0.1.0",
        "description": "OpenAI-compatible API gateway for Morpheus blockchain models"
    }

# Health check endpoint with Redis and PostgreSQL status
@app.get("/health")
async def health_check():
    # Check Redis connection
    redis_status = "ok" if test_redis_connection() else "unavailable"
    
    # Check database connection
    db_status = "ok"
    db_error = None
    try:
        # Try to connect to the database
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        db_status = "unavailable"
        db_error = str(e)
    
    # Mask the password in the URL for display
    db_url = str(settings.DATABASE_URL)
    if ":" in db_url and "@" in db_url:
        # Simple way to mask password in URL
        parts = db_url.split("@")
        user_pass = parts[0].split("://")[1].split(":")
        masked_url = f"{parts[0].split('://')[0]}://{user_pass[0]}:****@{parts[1]}"
    else:
        masked_url = db_url
    
    return {
        "status": "ok",
        "redis": redis_status,
        "database": db_status,
        "database_error": db_error,
        "database_url": masked_url
    }

# Custom OpenAPI schema generator
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # Add custom info about authentication
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
    
    if "securitySchemes" not in openapi_schema["components"]:
        openapi_schema["components"]["securitySchemes"] = {}
    
    # Add clear documentation for the Bearer token
    openapi_schema["components"]["securitySchemes"]["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Enter the JWT token you received from the login endpoint (without 'Bearer' prefix)"
    }

    # Add clear documentation for API Key auth
    openapi_schema["components"]["securitySchemes"]["APIKeyAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": "Authorization",
        "description": "Provide the API key with format: 'Bearer sk-xxxxxx'"
    }
    
    # Update security for specific paths
    for path_key, path_item in openapi_schema["paths"].items():
        # Skip authentication endpoints
        if "/auth/login" in path_key or "/auth/register" in path_key:
            continue
            
        # Add Bearer auth to all other auth endpoints
        if "/auth/" in path_key:
            for method, operation in path_item.items():
                operation["security"] = [{"BearerAuth": []}]
                
        # Add API Key auth to model and chat endpoints
        elif "/models" in path_key or "/chat" in path_key:
            for method, operation in path_item.items():
                operation["security"] = [{"APIKeyAuth": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

# TODO: Add other routers once implemented
# from src.api.v1.models import models_router
# from src.api.v1.chat import chat_router
# app.include_router(models_router, prefix=f"{settings.API_V1_STR}", tags=["Models"])
# app.include_router(chat_router, prefix=f"{settings.API_V1_STR}", tags=["Chat"]) 