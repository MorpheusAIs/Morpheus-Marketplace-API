"""
Minimal version of main.py for debugging startup issues
This strips out complex functionality to isolate the problem
"""

import logging
import os
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app with minimal configuration
app = FastAPI(
    title="Morpheus API Gateway - Debug Mode",
    description="Minimal version for debugging startup issues",
    version="0.1.0-debug"
)

@app.get("/", include_in_schema=True)
async def root():
    """Basic health check endpoint"""
    return {
        "name": "Morpheus API Gateway - Debug Mode",
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "message": "If you see this, the basic app is working!"
    }

@app.get("/health", include_in_schema=True)
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "debug_mode": True
    }

@app.get("/debug/env", include_in_schema=True)
async def debug_env():
    """Debug endpoint to check environment variables (non-sensitive only)"""
    env_vars = {}
    
    # Only show non-sensitive environment variables
    safe_vars = [
        'COGNITO_USER_POOL_ID',
        'COGNITO_CLIENT_ID', 
        'COGNITO_REGION',
        'COGNITO_DOMAIN',
        'AWS_REGION',
        'BASE_URL',
        'API_V1_STR'
    ]
    
    for var in safe_vars:
        value = os.getenv(var)
        env_vars[var] = value if value else "NOT_SET"
    
    return {
        "environment_variables": env_vars,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/debug/imports", include_in_schema=True)
async def debug_imports():
    """Test critical imports"""
    import_status = {}
    
    # Test basic imports
    try:
        import boto3
        import_status["boto3"] = "✅ OK"
    except Exception as e:
        import_status["boto3"] = f"❌ ERROR: {str(e)}"
    
    try:
        from jose import jwt
        import_status["python-jose"] = "✅ OK"
    except Exception as e:
        import_status["python-jose"] = f"❌ ERROR: {str(e)}"
    
    try:
        import asyncpg
        import_status["asyncpg"] = "✅ OK"
    except Exception as e:
        import_status["asyncpg"] = f"❌ ERROR: {str(e)}"
    
    try:
        from sqlalchemy.ext.asyncio import AsyncSession
        import_status["sqlalchemy"] = "✅ OK"
    except Exception as e:
        import_status["sqlalchemy"] = f"❌ ERROR: {str(e)}"
    
    return {
        "import_status": import_status,
        "timestamp": datetime.now().isoformat()
    }

# Add startup event handler
@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Minimal Morpheus API starting up...")
    logger.info("✅ Startup completed successfully!")

@app.on_event("shutdown") 
async def shutdown_event():
    logger.info("🛑 Minimal Morpheus API shutting down...")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
