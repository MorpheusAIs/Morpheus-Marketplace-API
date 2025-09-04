"""
Safe version of main.py that avoids potential startup timeout issues
This version removes complex startup operations and uses lazy loading
"""

import logging
import os
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import settings safely
try:
    from src.core.config import settings
    logger.info("✅ Settings imported successfully")
except Exception as e:
    logger.error(f"❌ Failed to import settings: {e}")
    # Create minimal settings for fallback
    class MinimalSettings:
        PROJECT_NAME = "Morpheus API Gateway"
        API_V1_STR = "/api/v1"
        BACKEND_CORS_ORIGINS = ["*"]
        BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
    settings = MinimalSettings()

# Create FastAPI app
app = FastAPI(
    title=settings.PROJECT_NAME,
    description="OpenAI-compatible API gateway for Morpheus blockchain models",
    version="0.1.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Basic health endpoints
@app.get("/", include_in_schema=True)
async def root():
    """Root endpoint returning basic API information."""
    return {
        "name": settings.PROJECT_NAME,
        "version": "0.1.0",
        "description": "OpenAI-compatible API gateway for Morpheus blockchain models",
        "status": "running",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health", include_in_schema=True)
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "morpheus-api-gateway"
    }

# Lazy loading function for routers
def load_routers():
    """Load routers lazily to avoid startup timeouts"""
    try:
        # Import routers only when needed
        from src.api.v1 import models, chat, embeddings, session, auth, automation, chat_history
        
        # Include basic routers first
        app.include_router(auth, prefix=f"{settings.API_V1_STR}/auth")
        app.include_router(models, prefix=f"{settings.API_V1_STR}")
        app.include_router(chat, prefix=f"{settings.API_V1_STR}/chat")
        app.include_router(embeddings, prefix=f"{settings.API_V1_STR}")
        app.include_router(session, prefix=f"{settings.API_V1_STR}/session")
        app.include_router(automation, prefix=f"{settings.API_V1_STR}/automation")
        app.include_router(chat_history, prefix=f"{settings.API_V1_STR}/chat-history")
        
        logger.info("✅ Basic routers loaded successfully")
        
        # Try to load the new cognito_auth router
        try:
            from src.api.v1.cognito_auth import router as cognito_auth
            app.include_router(cognito_auth, prefix=f"{settings.API_V1_STR}/auth")
            logger.info("✅ Cognito auth router loaded successfully")
        except Exception as e:
            logger.warning(f"⚠️ Could not load cognito_auth router: {e}")
            # Continue without it for now
        
        return True
    except Exception as e:
        logger.error(f"❌ Failed to load routers: {e}")
        return False

# Endpoint to trigger router loading
@app.get("/debug/load-routers")
async def debug_load_routers():
    """Debug endpoint to manually trigger router loading"""
    success = load_routers()
    return {
        "success": success,
        "message": "Routers loaded successfully" if success else "Failed to load routers",
        "timestamp": datetime.now().isoformat()
    }

# Simple docs endpoint
@app.get("/docs", include_in_schema=False)
def simple_docs():
    """Simple docs endpoint"""
    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{settings.PROJECT_NAME} - API Documentation</title>
        <link type="text/css" rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@4/swagger-ui.css">
    </head>
    <body>
        <div id="swagger-ui"></div>
        <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@4/swagger-ui-bundle.js"></script>
        <script>
            SwaggerUIBundle({{
                url: '/openapi.json',
                dom_id: '#swagger-ui',
                layout: 'BaseLayout'
            }});
        </script>
    </body>
    </html>
    """)

# Auth demo endpoint
@app.get("/auth-demo", include_in_schema=False)
async def auth_demo_page():
    """Authentication Demo Page"""
    return HTMLResponse(content="""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Morpheus API - Authentication Demo</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; text-align: center; }
            .container { max-width: 600px; margin: 0 auto; }
            .status { padding: 20px; background: #f0f0f0; border-radius: 8px; margin: 20px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 Morpheus API Gateway</h1>
            <div class="status">
                <h2>Service Status: Running</h2>
                <p>The API is running in safe mode.</p>
                <p><a href="/health">Health Check</a> | <a href="/docs">API Docs</a></p>
            </div>
            <p>Enhanced authentication features will be available once all components are loaded.</p>
        </div>
    </body>
    </html>
    """)

# Minimal startup event
@app.on_event("startup")
async def startup_event():
    """Minimal startup event to avoid timeouts"""
    logger.info("🚀 Morpheus API starting in safe mode...")
    
    # Try to load routers in the background, but don't block startup
    try:
        load_routers()
    except Exception as e:
        logger.warning(f"⚠️ Could not load all routers during startup: {e}")
        logger.info("💡 Routers can be loaded later via /debug/load-routers")
    
    logger.info("✅ Safe mode startup completed!")

@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown event"""
    logger.info("🛑 Morpheus API shutting down...")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
