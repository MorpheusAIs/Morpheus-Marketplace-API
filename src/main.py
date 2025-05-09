from fastapi import FastAPI, Request, status, Depends
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import time
import logging
import asyncio
import os
import pathlib
import datetime
from fastapi.routing import APIRoute, APIRouter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, text

from src.api.v1 import models, chat, session, auth, automation
from src.core.config import settings
from src.api.v1.custom_route import FixedDependencyAPIRoute
from src.db.models import Session as DbSession, Base
from src.services import session_service

# Add the import for testing database connection
from sqlalchemy.ext.asyncio import AsyncEngine
from src.db.database import engine

# Import what we need for proper SQL execution
from sqlalchemy import text

# Define log directory
log_dir = 'logs'
os.makedirs(log_dir, exist_ok=True) # Create log directory if it doesn't exist

# Set up detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'app.log')), # Use os.path.join
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Using our production-ready fixed route class
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

# Set our fixed dependency route class for all APIRouters
app.router.route_class = FixedDependencyAPIRoute

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
                    // Add custom CSS for animations
                    const style = document.createElement('style');
                    style.textContent = `
                        @keyframes pulse {
                            0% { box-shadow: 0 0 0 0 rgba(220, 53, 69, 0.7); }
                            70% { box-shadow: 0 0 0 10px rgba(220, 53, 69, 0); }
                            100% { box-shadow: 0 0 0 0 rgba(220, 53, 69, 0); }
                        }
                        .authorize.pulse {
                            animation: pulse 2s infinite;
                        }
                    `;
                    document.head.appendChild(style);
                    
                    // Add helpful instruction panel
                    const instructionDiv = document.createElement('div');
                    instructionDiv.innerHTML = `
                        <div style="background-color: #f8d7da; padding: 15px; margin-bottom: 20px; border-radius: 5px; border-left: 5px solid #dc3545;">
                            <h3 style="margin-top: 0; color: #721c24;">Authentication Required</h3>
                            <p><strong>⚠️ Two authentication methods available!</strong></p>
                            <p><strong>1. JWT Authentication (BearerAuth):</strong> For most endpoints except /session</p>
                            <p>- Register or Login using the /auth/register or /auth/login endpoints</p>
                            <p>- Copy the access_token from the response</p>
                            <p>- Click "Authorize" and paste the token in the BearerAuth field (without "Bearer" prefix)</p>
                            <p><strong>2. API Key Authentication (APIKeyAuth):</strong> For /session endpoints</p>
                            <p>- Create an API key via /auth/keys endpoint (requires JWT auth)</p>
                            <p>- Copy the API key from the response</p>
                            <p>- Click "Authorize" and paste the API key in the APIKeyAuth field</p>
                            <p>- The API key can be entered as either "Bearer sk-xxxxxx" or just "sk-xxxxxx"</p>
                        </div>
                    `;
                    
                    // Add it to the top of the Swagger UI for better visibility
                    const swaggerUI = document.querySelector('.swagger-ui');
                    const infoContainer = swaggerUI.querySelector('.information-container');
                    infoContainer.after(instructionDiv);
                    
                    // Fix session endpoints to use API key auth when the page loads
                    setTimeout(function() {
                        const authButton = document.querySelector('.authorize');
                        if (!localStorage.getItem('auth_reminded')) {
                            authButton.classList.add('pulse');
                            localStorage.setItem('auth_reminded', 'true');
                        }
                        
                        // Check if we're on a session endpoint and select the right auth
                        const pathElements = document.querySelectorAll('.opblock-summary-path');
                        pathElements.forEach(function(elem) {
                            const path = elem.innerText;
                            if (path && path.includes('/session/')) {
                                const opblock = elem.closest('.opblock');
                                if (opblock) {
                                    const authEl = opblock.querySelector('.authorization__btn');
                                    if (authEl) {
                                        // Add a visual indicator for API Key auth
                                        const indicator = document.createElement('span');
                                        indicator.className = 'api-key-indicator';
                                        indicator.innerText = 'API Key';
                                        indicator.style.backgroundColor = '#28a745';
                                        indicator.style.color = 'white';
                                        indicator.style.padding = '2px 8px';
                                        indicator.style.borderRadius = '4px';
                                        indicator.style.fontSize = '10px';
                                        indicator.style.marginLeft = '8px';
                                        authEl.appendChild(indicator);
                                    }
                                }
                            }
                        });
                    }, 1000);
                }
            """
        }
    )

# Set up Jinja2 templates
templates_path = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_path))

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

# Background task to clean up expired sessions
async def cleanup_expired_sessions():
    """
    Background task to clean up expired sessions and synchronize session states.
    """
    from src.db.models import Session as DbSession
    from sqlalchemy import select
    from src.services import session_service
    from src.db.database import AsyncSessionLocal, engine
    from sqlalchemy.ext.asyncio import AsyncSession
    import traceback
    
    logger = logging.getLogger("session_cleanup")
    logger.info("Starting expired session cleanup task")
    
    while True:
        try:
            # Log connection attempt for debugging
            logger.info("Attempting to connect to database for session cleanup")
            
            async with AsyncSessionLocal() as db:
                # Find expired active sessions
                now_with_tz = datetime.now(timezone.utc)
                # Convert to naive datetime for DB compatibility
                now = now_with_tz.replace(tzinfo=None)
                result = await db.execute(
                    select(DbSession)
                    .where(DbSession.is_active == True, DbSession.expires_at < now)
                )
                expired_sessions = result.scalars().all()
                
                if expired_sessions:
                    logger.info(f"Found {len(expired_sessions)} expired sessions to clean up")
                    
                    # Process each expired session
                    for session in expired_sessions:
                        logger.info(f"Cleaning up expired session {session.id}")
                        await session_service.close_session(db, session.id)
                else:
                    logger.info("No expired sessions found to clean up")
                
                # Synchronize session states between database and proxy router
                try:
                    logger.info("Starting session state synchronization")
                    await session_service.synchronize_sessions(db)
                    logger.info("Session state synchronization completed")
                except Exception as sync_error:
                    logger.error(f"Error during session synchronization: {str(sync_error)}")
                    logger.error(traceback.format_exc())
        
        except Exception as e:
            logger.error(f"Error in session cleanup task: {str(e)}")
            logger.error(traceback.format_exc())
        
        # Run every 15 minutes
        await asyncio.sleep(15 * 60)

# Application startup event
@app.on_event("startup")
async def startup_event():
    """
    Perform startup initialization.
    """
    # Verify database migrations are up to date
    await verify_database_migrations()
    
    # Make sure all routers use our fixed route class
    for router in [auth, models, chat, session, automation]:
        update_router_route_class(router, FixedDependencyAPIRoute)
    
    logger.info("Application startup complete. Using FixedDependencyAPIRoute for all routes.")
    
    # Start the background tasks
    asyncio.create_task(cleanup_expired_sessions())
    logger.info("Started background task for expired session cleanup")

async def verify_database_migrations():
    """
    Verify that database migrations are up to date.
    """
    try:
        # Import what we need to check migration revisions
        from alembic.script import ScriptDirectory
        from alembic.config import Config
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession
        from src.db.database import engine
        import os
        
        # Get the expected head revision
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
        config = Config(config_path)
        script = ScriptDirectory.from_config(config)
        head_revision = script.get_current_head()
        
        logger.info(f"Checking database migrations (expected head: {head_revision})")
        
        # Get the current database revision
        async with AsyncSession(engine) as session:
            try:
                result = await session.execute(text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'alembic_version')"))
                table_exists = result.scalar()
                
                if not table_exists:
                    logger.error("Database not initialized: alembic_version table doesn't exist")
                    logger.error("Please run 'alembic upgrade head' to initialize the database")
                    return
                
                result = await session.execute(text("SELECT version_num FROM alembic_version"))
                current_revision = result.scalar_one_or_none()
                
                if current_revision != head_revision:
                    logger.error(f"Database schema out of date. Current: {current_revision}, Expected: {head_revision}")
                    logger.error("Please run 'alembic upgrade head' to update your database schema")
                else:
                    logger.info(f"Database schema is up to date (revision: {current_revision})")
                    
                # Also check if the sessions table exists
                result = await session.execute(text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'sessions')"))
                sessions_exists = result.scalar()
                
                if not sessions_exists:
                    logger.error("Sessions table doesn't exist despite migrations being up to date")
                    logger.error("There may be an issue with your migrations - consider recreating the database")
            except Exception as db_error:
                logger.error(f"Error checking migration status: {db_error}")
    except Exception as e:
        logger.error(f"Failed to verify migrations: {e}")
        logger.error("Continuing startup, but there may be database schema issues")

# Update router route classes
def update_router_route_class(router: APIRouter, route_class=FixedDependencyAPIRoute):
    """
    Update an APIRouter instance to use our fixed route class.
    
    This is used to propagate the route class to all included routers.
    
    Args:
        router: The router to update
        route_class: The route class to use
    """
    router.route_class = route_class
    for route in router.routes:
        if isinstance(route, APIRouter):
            update_router_route_class(route, route_class)
    return router

# Update all imported routers with our custom route class
update_router_route_class(auth)
update_router_route_class(models)
update_router_route_class(chat)
update_router_route_class(session)
update_router_route_class(automation)

# Include routers
app.include_router(auth, prefix=f"{settings.API_V1_STR}/auth")
app.include_router(models, prefix=f"{settings.API_V1_STR}/models")
app.include_router(chat, prefix=f"{settings.API_V1_STR}/chat")
app.include_router(session, prefix=f"{settings.API_V1_STR}/session")
app.include_router(automation, prefix=f"{settings.API_V1_STR}/automation")

# Default routes - using standard APIRoute for these endpoints to avoid dependency resolution issues
# Reset the route_class temporarily for these specific routes
original_route_class = app.router.route_class
app.router.route_class = APIRoute

@app.get("/", include_in_schema=True)
async def root():
    """
    Root endpoint returning basic API information.
    """
    return {
        "name": settings.PROJECT_NAME,
        "version": "0.1.0",
        "description": "OpenAI-compatible API gateway for Morpheus blockchain models",
        "documentation": {
            "swagger_ui": "/docs"
        }
    }

@app.get("/health", include_in_schema=True)
async def health_check():
    """
    Health check endpoint to verify API and database status.
    """
    # Check database connection
    try:
        # Connect to the database and execute a simple query
        await check_db_connection(engine)
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
    
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "version": "0.1.0",
        "database": db_status
    }

# Restore the original route class for subsequent routes
app.router.route_class = original_route_class

# Check database connection (async)
async def check_db_connection(engine: AsyncEngine):
    """Check if database connection is working"""
    from sqlalchemy.ext.asyncio import AsyncSession
    
    async with AsyncSession(engine) as session:
        # Execute a simple query
        result = await session.execute(text("SELECT 1"))
        return result.scalar() == 1

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
        "description": "Provide the API key in either format: 'Bearer sk-xxxxxx.yyyyyyy' or just 'sk-xxxxxx.yyyyyyy'. The prefix is 9 characters long including 'sk-'."
    }
    
    # Update security for specific paths and completely remove args and kwargs parameters
    for path_key, path_item in openapi_schema["paths"].items():
        # Process each operation (GET, POST, etc.)
        for method, operation in path_item.items():
            # Remove args and kwargs parameters from all routes
            if "parameters" in operation:
                # Remove args and kwargs parameters entirely
                operation["parameters"] = [
                    param for param in operation["parameters"]
                    if param.get("name") not in ["args", "kwargs"]
                ]
                
                # If no parameters are left, remove the empty parameters list
                if not operation["parameters"]:
                    del operation["parameters"]
            
            # IMPORTANT: Check if there are required properties in the schema and remove args and kwargs
            if "requestBody" in operation and "content" in operation["requestBody"]:
                for content_type, content_schema in operation["requestBody"]["content"].items():
                    if "schema" in content_schema and "properties" in content_schema["schema"]:
                        # Remove args and kwargs from properties
                        if "args" in content_schema["schema"]["properties"]:
                            del content_schema["schema"]["properties"]["args"]
                        if "kwargs" in content_schema["schema"]["properties"]:
                            del content_schema["schema"]["properties"]["kwargs"]
                        
                        # Remove args and kwargs from required list
                        if "required" in content_schema["schema"]:
                            content_schema["schema"]["required"] = [
                                prop for prop in content_schema["schema"]["required"] 
                                if prop not in ["args", "kwargs"]
                            ]
        
            # Add example for chat completions endpoint
            if path_key == "/api/v1/chat/completions" and method == "post" and "requestBody" in operation:
                for content_type in operation["requestBody"]["content"]:
                    operation["requestBody"]["content"][content_type]["example"] = {
                        "model": "default",
                        "messages": [
                            {"role": "system", "content": "You are a helpful assistant."},
                            {"role": "user", "content": "Hello, how are you?"}
                        ],
                        "temperature": 0.7,
                        "stream": True
                    }
                    
                    # Add description about session_id
                    if "schema" in operation["requestBody"]["content"][content_type]:
                        schema = operation["requestBody"]["content"][content_type]["schema"]
                        description = schema.get("description", "")
                        schema["description"] = description + "\n\nNote: You can optionally include 'session_id' if you want to use a specific session."
            
            # Add example for automation settings endpoint
            if path_key == "/api/v1/automation/settings" and method == "put" and "requestBody" in operation:
                for content_type in operation["requestBody"]["content"]:
                    operation["requestBody"]["content"][content_type]["example"] = {
                        "is_enabled": True,
                        "session_duration": 3600
                    }
                    
                    # Add description about session_duration
                    if "schema" in operation["requestBody"]["content"][content_type]:
                        schema = operation["requestBody"]["content"][content_type]["schema"]
                        description = schema.get("description", "")
                        schema["description"] = description + "\n\nNote: session_duration is in seconds. Default is 3600 (1 hour). Min: 60, Max: 86400 (24 hours)."
        
            # Determine which security scheme to apply based on the endpoint
            if path_key.startswith("/api/v1/auth/login") or path_key.startswith("/api/v1/auth/register"):
                # No security for login/register endpoints
                pass
            elif path_key.startswith("/api/v1/session/") or path_key == "/api/v1/chat/completions":
                # Apply API Key authentication to session endpoints and chat completions
                for method in path_item:
                    path_item[method]["security"] = [{"APIKeyAuth": []}]
            else:
                # Apply JWT Bearer authentication to all other endpoints
                for method in path_item:
                    path_item[method]["security"] = [{"BearerAuth": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema

# Set custom OpenAPI schema generator
app.openapi = custom_openapi 

# API Documentation landing page
@app.get("/api-docs", include_in_schema=False)
async def api_docs_landing(request: Request):
    """
    Landing page for API docs
    """
    return templates.TemplateResponse(
        "api_docs_landing.html",
        {
            "request": request,
            "title": app.title,
            "year": datetime.now().year
        }
    )

# The test-private-key endpoint has been removed

# The set-private-key endpoint has been removed 

@app.on_event("startup")
async def init_db():
    """Initialize database tables on startup"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all) 