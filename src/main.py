from fastapi import FastAPI, Request, status, Depends
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, APIRouter
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
import time
import logging
import asyncio
import os
import uuid
import socket
import platform

from src.api.v1 import models, chat, session, auth, automation, chat_history
from src.core.config import settings
from src.core.version import get_version, get_version_info
from src.core.cors_middleware import CredentialSafeCORSMiddleware
from src.api.v1.custom_route import FixedDependencyAPIRoute
from src.db.models import Session as DbSession
from src.services import session_service
from src.db.database import engine, get_db
from src.core.direct_model_service import direct_model_service

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

# Global variables for container diagnostics
APP_START_TIME = None
CONTAINER_ID = str(uuid.uuid4())
APP_VERSION = get_version()

# Using our production-ready fixed route class
app = FastAPI(
    title="Morpheus API Gateway",
    description="API Gateway connecting Web2 clients to the Morpheus-Lumerin AI Marketplace",
    version=APP_VERSION,
    redirect_slashes=False,  # Disable automatic redirects to prevent HTTPS→HTTP downgrade attacks
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=None,  # Disable default docs so we can customize it
    redoc_url="/redoc",  # Re-enable ReDoc for alternative documentation
    swagger_ui_oauth2_redirect_url="/docs/oauth2-redirect"
)

# Set our fixed dependency route class for all APIRouters
app.router.route_class = FixedDependencyAPIRoute

# Note: Custom OpenAPI function is defined later in the file

# Set up CORS with credential-safe configuration
try:
    # Use new CORS_ALLOWED_ORIGINS setting (preferred)
    allowed_origins = settings.CORS_ALLOWED_ORIGINS
    
    app.add_middleware(
        CredentialSafeCORSMiddleware,
        allowed_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-API-Key"],
        expose_headers=["Content-Length", "Content-Type"],
        max_age=86400,  # 24 hours for preflight cache
        trusted_domain_patterns=[
            r"^https://.*\.mor\.org$",  # Any subdomain of mor.org
            r"^https://.*\.dev\.mor\.org$",  # Any subdomain of dev.mor.org
        ],
        allow_direct_access=True  # Enable for ALB cookie stickiness from any client
    )
    
    core_log.info(f"CORS configured with allowed origins: {', '.join(allowed_origins)}")
    
except Exception as e:
    # Fallback to legacy CORS configuration for development
    core_log.warning(f"Failed to configure new CORS middleware: {e}")
    core_log.warning("Falling back to legacy CORS configuration")
    
    if hasattr(settings, 'BACKEND_CORS_ORIGINS'):
        origins = []
        if isinstance(settings.BACKEND_CORS_ORIGINS, list):
            origins = settings.BACKEND_CORS_ORIGINS
        elif isinstance(settings.BACKEND_CORS_ORIGINS, str):
            origins = [settings.BACKEND_CORS_ORIGINS]
        
        # Never use wildcard with credentials in production
        if origins and origins[0] == "*":
            core_log.warning("Using wildcard CORS origins - this should only be used in development")
            app.add_middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_credentials=False,  # Disable credentials with wildcard
                allow_methods=["*"],
                allow_headers=["*"],
            )
        else:
            # Use specified origins with credentials
            app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
    else:
        # Development fallback
        core_log.warning("No CORS origins configured - using development defaults")
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,  # Disable credentials with wildcard
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

# HTTPS enforcement middleware
@app.middleware("http")
async def enforce_https(request: Request, call_next):
    """
    Enforce HTTPS in production environments.
    Proxy-aware: Checks X-Forwarded-Proto to determine original protocol.
    """
    # Allow HTTP for localhost/development
    if (request.url.hostname in ["localhost", "127.0.0.1"] or 
        request.url.hostname.startswith("192.168.") or
        request.url.hostname.startswith("10.") or
        request.url.hostname.startswith("172.")):
        return await call_next(request)
    
    # Check for proxy headers to determine original protocol
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").lower()
    forwarded_scheme = request.headers.get("X-Forwarded-Scheme", "").lower()
    cf_visitor = request.headers.get("CF-Visitor", "")
    
    # Determine if the original request was HTTPS
    original_was_https = (
        forwarded_proto == "https" or
        forwarded_scheme == "https" or
        '"scheme":"https"' in cf_visitor or  # CloudFlare format
        request.url.scheme == "https"
    )
    
    # Only enforce HTTPS if the original request was HTTP (not HTTPS)
    if not original_was_https and request.url.scheme == "http":
        https_url = request.url.replace(scheme="https")
        return JSONResponse(
            status_code=426,
            content={
                "error": "HTTPS Required",
                "message": "This API requires HTTPS. Please use the secure endpoint.",
                "https_url": str(https_url)
            }
        )
    
    return await call_next(request)

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

@app.on_event("startup")
async def startup_event():
    """
    Perform startup initialization.
    """
    global APP_START_TIME
    APP_START_TIME = datetime.utcnow()
    
    logger.info("🔄 Starting Morpheus API Gateway startup sequence...")
    logger.info(f"📊 Configuration: Direct model fetching from {settings.ACTIVE_MODELS_URL}")
    logger.info(f"🏷️ Container ID: {CONTAINER_ID}")
    logger.info(f"📦 Version: {APP_VERSION}")
    
    # Log local testing status
    from src.core.local_testing import log_local_testing_status
    log_local_testing_status()
    
    # All workers perform lightweight checks - no complex coordination needed
    worker_pid = os.getpid()
    logger.info(f"🔧 Worker PID: {worker_pid}")
    
    try:
        # Temporarily skip database version check to resolve startup issues
        # TODO: Re-enable after resource issues are resolved
        logger.info("⏩ Temporarily skipping database version check to resolve startup timeouts")
        
        # Only first worker checks database version to prevent connection pool exhaustion
        # if worker_pid % 4 == 0:  # Only one worker does DB version check
        #     logger.info("🗃️ Checking database version compatibility...")
        #     await check_database_version()
        # else:
        #     logger.info("⏩ Skipping database version check in this worker to prevent connection contention")
        
        # Initialize direct model service with memory-conscious approach
        logger.info("🤖 Initializing direct model service...")
        try:
            # Stagger model fetching to reduce concurrent requests (shorter delays to avoid timeout)
            stagger_delay = (worker_pid % 4) * 0.5  # 0, 0.5, 1.0, 1.5 second delays
            if stagger_delay > 0:
                logger.info(f"⏳ Staggering model fetch by {stagger_delay}s to reduce concurrent requests")
                await asyncio.sleep(stagger_delay)
            
            # Test initial fetch to ensure service is working
            models = await direct_model_service.get_model_mapping()
            logger.info(f"✅ Direct model service initialized with {len(models)} models")
        except Exception as e:
            logger.error(f"❌ Failed to initialize direct model service: {e}")
            logger.warning("Continuing startup - model service will retry on first request")
        
    except Exception as e:
        logger.error(f"❌ Error during worker initialization: {e}")
        # For database version mismatches, we want to fail fast
        if "Database version mismatch" in str(e):
            logger.error("🚨 Database version incompatible - failing startup")
            raise e
        logger.warning("Continuing startup with minimal initialization")
    
    # Make sure all routers use our fixed route class
    try:
        for router in [auth, models, chat, session, automation, chat_history]:
            update_router_route_class(router, FixedDependencyAPIRoute)
        logger.info("✅ All routers configured with FixedDependencyAPIRoute")
    except Exception as e:
        logger.error(f"❌ Error configuring routers: {e}")
        logger.warning("Continuing startup with default route classes...")
    
    # Start the background tasks
    try:
        asyncio.create_task(cleanup_expired_sessions())
        logger.info("✅ Started background task for expired session cleanup")
    except Exception as e:
        logger.error(f"❌ Failed to start background cleanup task: {e}")
        logger.warning("Continuing startup without background session cleanup...")
    
    logger.info("🚀 Application startup complete!")

@app.on_event("shutdown")
async def shutdown_event():
    """
    Perform cleanup during application shutdown.
    """
    logger.info("🛑 Application shutdown initiated...")
    logger.info("✅ Direct model service requires no cleanup (stateless)")
    logger.info("🏁 Application shutdown complete")

async def check_database_version():
    """
    Lightweight check to verify database schema version matches expectations.
    This ensures the application doesn't start with an incompatible database schema.
    CI/CD should handle migrations - this just verifies they completed successfully.
    """
    try:
        logger.info("🔍 Checking database version compatibility...")
        
        # Import what we need to check migration revisions
        from alembic.script import ScriptDirectory
        from alembic.config import Config
        from sqlalchemy import text
        from src.db.database import engine
        import os
        
        # Get the alembic config
        alembic_cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
        config = Config(alembic_cfg_path)
        script_dir = ScriptDirectory.from_config(config)
        
        # Get the expected revision (what this app version expects)
        expected_revision = script_dir.get_current_head()
        logger.info(f"📋 Expected database version: {expected_revision}")
        
        # Connect to database and check current revision
        async with engine.begin() as conn:
            # Check if alembic_version table exists
            result = await conn.execute(text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name='alembic_version')"
            ))
            table_exists = result.scalar()
            
            if not table_exists:
                error_msg = "❌ Alembic version table doesn't exist - database not initialized or CI/CD migration failed"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            
            # Get current database revision
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            current_revision = result.scalar()
            
            if current_revision is None:
                error_msg = "❌ No migration version found in database - CI/CD migration may have failed"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
                
            logger.info(f"🗄️ Current database version: {current_revision}")
            
            # Compare revisions - must match exactly
            if current_revision == expected_revision:
                logger.info("✅ Database version matches expected version")
            else:
                error_msg = f"❌ Database version mismatch! Expected '{expected_revision}' but got '{current_revision}'. CI/CD migration may not have completed successfully. Please check the deployment pipeline."
                logger.error(error_msg)
                raise RuntimeError(error_msg)
                
    except Exception as e:
        logger.error(f"❌ Database version check failed: {str(e)}")
        # Fail fast if database version is incompatible
        raise RuntimeError(f"Database version check failed: {str(e)}")
    finally:
        logger.info("Database version check completed")

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
update_router_route_class(chat_history)

# Include routers
app.include_router(auth, prefix=f"{settings.API_V1_STR}/auth")
app.include_router(models, prefix=f"{settings.API_V1_STR}")  # Mount at /api/v1 and let models handle /models
app.include_router(chat, prefix=f"{settings.API_V1_STR}/chat")
app.include_router(session, prefix=f"{settings.API_V1_STR}/session")
app.include_router(automation, prefix=f"{settings.API_V1_STR}/automation")
app.include_router(chat_history, prefix=f"{settings.API_V1_STR}/chat-history")



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
        "version": APP_VERSION,
        "description": "OpenAI-compatible API gateway for Morpheus blockchain models",
        "documentation": {
            "swagger_ui": "/docs"
        }
    }

@app.get("/health", include_in_schema=True)
async def health_check():
    """
    Health check endpoint with container diagnostics for deployment monitoring.
    
    Returns system health, uptime, and unique container identifier for support and log analysis.
    Note: No sensitive AWS or hostname information is exposed.
    """
    current_time = datetime.utcnow()
    
    # Check database connection
    try:
        await check_db_connection(engine)
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
    
    # Check model service health
    model_service_status = "healthy"
    model_count = 0
    model_cache_info = {}
    try:
        # Test model service connectivity
        models = await direct_model_service.get_model_mapping()
        model_count = len(models)
        model_cache_info = direct_model_service.get_cache_stats()
        
        if model_count == 0:
            model_service_status = "warning: no models available"
        
    except Exception as e:
        model_service_status = f"unhealthy: {str(e)}"
    
    # Calculate uptime
    uptime_seconds = None
    uptime_human = None
    if APP_START_TIME:
        uptime_delta = current_time - APP_START_TIME
        uptime_seconds = int(uptime_delta.total_seconds())
        
        # Human-readable uptime
        days = uptime_delta.days
        hours, remainder = divmod(uptime_delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        uptime_parts = []
        if days > 0:
            uptime_parts.append(f"{days}d")
        if hours > 0:
            uptime_parts.append(f"{hours}h")
        if minutes > 0:
            uptime_parts.append(f"{minutes}m")
        if seconds > 0 or not uptime_parts:
            uptime_parts.append(f"{seconds}s")
        
        uptime_human = " ".join(uptime_parts)
    
    # Get basic system information (non-sensitive)
    try:
        # Get just the kernel version without AWS-specific details
        kernel_info = platform.release()  # e.g., "5.10.238"
        system_info = f"Linux-{kernel_info}"
    except:
        system_info = "Unknown"
    
    response = {
        "status": "ok",
        "timestamp": current_time.isoformat(),
        "version": APP_VERSION,
        "database": db_status,
        "model_service": {
            "status": model_service_status,
            "model_count": model_count,
            "cache_info": model_cache_info,
            "active_models_url": settings.ACTIVE_MODELS_URL,
            "default_fallback_model": settings.DEFAULT_FALLBACK_MODEL
        },
        "container": {
            "id": CONTAINER_ID,
            "system": system_info,
            "python_version": platform.python_version()
        },
        "uptime": {
            "seconds": uptime_seconds,
            "human_readable": uptime_human,
            "started_at": APP_START_TIME.isoformat() if APP_START_TIME else None
        }
    }
    
    return response

@app.get("/health/models", include_in_schema=True)
async def model_health_check():
    """
    Detailed model service health check for monitoring and debugging.
    
    Returns comprehensive information about the model fetching service,
    cache status, and available models for operational monitoring.
    """
    try:
        # Get model service statistics
        model_mapping = await direct_model_service.get_model_mapping()
        blockchain_ids = await direct_model_service.get_blockchain_ids()
        raw_models = await direct_model_service.get_raw_models_data()
        cache_stats = direct_model_service.get_cache_stats()
        
        # Test model resolution for common models
        test_results = {}
        test_models = ["venice-uncensored", "mistral-31-24b", "gpt-4", "default"]
        for test_model in test_models:
            try:
                resolved_id = await direct_model_service.resolve_model_id(test_model)
                test_results[test_model] = {
                    "status": "resolved" if resolved_id else "not_found",
                    "blockchain_id": resolved_id
                }
            except Exception as e:
                test_results[test_model] = {
                    "status": "error",
                    "error": str(e)
                }
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "service_config": {
                "active_models_url": settings.ACTIVE_MODELS_URL,
                "default_fallback_model": settings.DEFAULT_FALLBACK_MODEL,
                "cache_duration_seconds": cache_stats.get("cache_duration", "unknown")
            },
            "cache_stats": cache_stats,
            "model_counts": {
                "total_models": len(raw_models),
                "active_mappings": len(model_mapping),
                "blockchain_ids": len(blockchain_ids)
            },
            "test_results": test_results,
            "available_models": sorted(list(model_mapping.keys()))[:20],  # First 20 models
            "sample_blockchain_ids": sorted(list(blockchain_ids))[:10]  # First 10 IDs
        }
        
    except Exception as e:
        return {
            "status": "unhealthy",
            "timestamp": datetime.utcnow().isoformat(),
            "error": str(e),
            "service_config": {
                "active_models_url": settings.ACTIVE_MODELS_URL,
                "default_fallback_model": settings.DEFAULT_FALLBACK_MODEL
            }
        }

@app.get("/cors-check", include_in_schema=True)
async def cors_check(request: Request):
    """
    CORS configuration test endpoint for ALB lb_cookie stickiness verification.
    
    This endpoint helps verify that CORS is properly configured for cross-origin
    requests with credentials, which is required for AWS ALB sticky sessions.
    
    Returns CORS configuration details and request information for debugging.
    """
    origin = request.headers.get("origin")
    user_agent = request.headers.get("user-agent", "")
    
    # Get CORS configuration
    cors_config = {
        "explicit_origins": settings.CORS_ALLOWED_ORIGINS,
        "credentials_enabled": True,
        "allowed_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        "allowed_headers": ["Authorization", "Content-Type", "X-Requested-With", "X-API-Key"],
        "exposed_headers": ["Content-Length", "Content-Type"],
        "trusted_patterns": [
            "^https://.*\\.mor\\.org$",
            "^https://.*\\.dev\\.mor\\.org$"
        ],
        "direct_access_enabled": True,
        "direct_access_note": "Any HTTPS origin allowed for ALB cookie stickiness"
    }
    
    # Check if the current origin would be allowed
    origin_allowed = False
    origin_type = "none"
    if origin:
        # Simulate the middleware logic
        if origin in settings.CORS_ALLOWED_ORIGINS:
            origin_allowed = True
            origin_type = "explicit"
        else:
            # Check patterns
            import re
            patterns = [r"^https://.*\.mor\.org$", r"^https://.*\.dev\.mor\.org$"]
            for pattern in patterns:
                if re.match(pattern, origin):
                    origin_allowed = True
                    origin_type = "trusted_pattern"
                    break
            
            # Check direct access (HTTPS origins)
            if not origin_allowed:
                from urllib.parse import urlparse
                try:
                    parsed = urlparse(origin)
                    if parsed.scheme == 'https':
                        origin_allowed = True
                        origin_type = "direct_https"
                    elif parsed.scheme == 'http' and parsed.hostname in ['localhost', '127.0.0.1']:
                        origin_allowed = True
                        origin_type = "direct_http_local"
                except:
                    pass
    
    response_data = {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "message": "CORS check endpoint - verify headers in browser dev tools",
        "request_info": {
            "origin": origin,
            "origin_allowed": origin_allowed,
            "origin_type": origin_type,
            "method": request.method,
            "user_agent": user_agent[:100] + "..." if len(user_agent) > 100 else user_agent
        },
        "cors_config": cors_config,
        "instructions": {
            "browser_test": "Open browser dev tools, check Network tab for CORS headers",
            "expected_headers": [
                "Access-Control-Allow-Origin: <your-origin>",
                "Access-Control-Allow-Credentials: true",
                "Vary: Origin"
            ],
            "curl_test": "curl -H 'Origin: https://openbeta.mor.org' -v https://api.mor.org/cors-check"
        }
    }
    
    return response_data

# Custom docs endpoints using standard APIRoute
@app.get("/docs/oauth2-redirect", include_in_schema=False)
async def swagger_ui_oauth2_redirect(request: Request):
    """
    OAuth2 redirect endpoint that automatically exchanges code for token and integrates with Swagger UI.
    """
    import httpx
    
    # Extract the authorization code and state
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")
    
    if error:
        return HTMLResponse(content=f"""
        <!DOCTYPE html>
        <html>
        <head><title>OAuth2 Error</title></head>
        <body>
            <h1>OAuth2 Authentication Error</h1>
            <p><strong>Error:</strong> {error}</p>
            <p><strong>Description:</strong> {request.query_params.get("error_description", "Unknown error")}</p>
            <p><a href="/docs">Return to API Documentation</a></p>
        </body>
        </html>
        """)
    
    # If we have a code, exchange it for an access token
    access_token = None
    token_error = None
    if code:
        try:
            # Exchange the authorization code for tokens
            token_url = f"https://{settings.COGNITO_DOMAIN}/oauth2/token"
            
            data = {
                "grant_type": "authorization_code",
                "client_id": settings.COGNITO_CLIENT_ID,
                "code": code,
                "redirect_uri": f"{settings.BASE_URL}/docs/oauth2-redirect"
            }
            
            headers = {
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(token_url, data=data, headers=headers)
                
            if response.status_code == 200:
                tokens = response.json()
                access_token = tokens.get("access_token")
                logger.info("Token exchange successful")
            else:
                error_body = response.text
                logger.warning(f"Token exchange failed - Status: {response.status_code}")
                logger.warning("Token exchange error response received")
                token_error = f"HTTP {response.status_code}: {error_body}"
                
        except Exception as e:
            logger.error(f"Token exchange exception: {str(e)}")
            token_error = str(e)
    
    # Build the HTML with proper JavaScript variable interpolation
    js_access_token = f'"{access_token}"' if access_token else '""'
    js_auth_code = f'"{code}"' if code else '""'
    js_state = f'"{state}"' if state else '""'
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en-US">
    <head>
        <title>OAuth2 Redirect</title>
        <style>
            body {{ 
                font-family: Arial, sans-serif; 
                padding: 40px; 
                text-align: center;
                background: #f8f9fa;
            }}
            .success {{ color: #28a745; }}
            .spinner {{ 
                border: 4px solid #f3f3f3; 
                border-top: 4px solid #28a745; 
                border-radius: 50%; 
                width: 40px; 
                height: 40px; 
                animation: spin 1s linear infinite; 
                margin: 20px auto; 
            }}
            @keyframes spin {{ 
                0% {{ transform: rotate(0deg); }} 
                100% {{ transform: rotate(360deg); }} 
            }}
            .token-display {{
                background: #f8f9fa;
                border: 2px solid #28a745;
                border-radius: 8px;
                padding: 15px;
                margin: 20px auto;
                max-width: 600px;
                word-break: break-all;
                font-family: monospace;
                font-size: 12px;
            }}
        </style>
    </head>
    <body>
        <h1 class="success">✅ Authentication Successful!</h1>
        <div class="spinner" id="spinner"></div>
        <p id="status">Processing OAuth2 authentication...</p>
        
        <script>
            'use strict';
            
            const accessToken = {js_access_token};
            const authCode = {js_auth_code};
            const authState = {js_state};
            
            function run() {{
                console.log('🔍 OAuth2 redirect processing...');
                console.log('🔑 Access token available:', accessToken ? 'Yes' : 'No');
                console.log('🔍 Authorization code:', authCode ? 'Present' : 'Missing');
                console.log('🪟 Window opener:', window.opener ? 'Present' : 'Null');
                
                // Hide spinner
                document.getElementById('spinner').style.display = 'none';
                
                // Try to handle as popup first
                console.log('🔍 Popup detection:', {{
                    hasOpener: !!window.opener,
                    hasSwaggerCallback: !!(window.opener && window.opener.swaggerUIRedirectOauth2)
                }});
                
                if (window.opener && window.opener.swaggerUIRedirectOauth2) {{
                    console.log('🔄 Handling as popup window');
                    try {{
                        const oauth2 = window.opener.swaggerUIRedirectOauth2;
                        
                        // If we have an access token, pass it directly
                        if (accessToken) {{
                            console.log('✅ Passing access token to Swagger UI');
                            oauth2.callback({{
                                auth: oauth2.auth,
                                token: {{
                                    access_token: accessToken,
                                    token_type: 'Bearer'
                                }},
                                redirectUrl: oauth2.redirectUrl
                            }});
                        }} else {{
                            // Fall back to code-based flow
                            oauth2.callback({{
                                auth: oauth2.auth,
                                code: authCode,
                                state: authState,
                                redirectUrl: oauth2.redirectUrl
                            }});
                        }}
                        
                        document.getElementById('status').innerHTML = `
                            <div>
                                <h2 style="color: #28a745;">✅ Authentication Complete!</h2>
                                <p>Token has been applied to the main window.</p>
                                <button onclick="window.close()" style="background: #007bff; color: white; padding: 12px 24px; border: none; border-radius: 6px; font-size: 16px; cursor: pointer; margin-top: 15px;">
                                    Close Window
                                </button>
                                <p style="color: #6c757d; margin-top: 10px; font-size: 14px;">Window will close automatically in 3 seconds...</p>
                            </div>
                        `;
                        setTimeout(() => window.close(), 3000);
                        return;
                    }} catch (e) {{
                        console.error('❌ Popup callback error:', e);
                    }}
                }}
                
                // Handle as new tab OR popup - simplified approach
                console.log('🔄 Handling authentication completion');
                
                if (accessToken) {{
                    // Store token in localStorage 
                    console.log('✅ Storing token in localStorage...');
                    localStorage.setItem('swagger_oauth_token', accessToken);
                    localStorage.setItem('swagger_oauth_token_timestamp', Date.now().toString());
                    
                    // Always show close button - no redirect, no detection needed
                    console.log('🔄 Showing close button');
                    document.getElementById('status').innerHTML = `
                        <div>
                            <h2 style="color: #28a745;">✅ Authentication Complete!</h2>
                            <p>Token has been applied to the main window.</p>
                            <button onclick="window.close()" style="background: #6c757d; color: white; padding: 12px 24px; border: none; border-radius: 6px; font-size: 16px; cursor: pointer; margin-top: 15px;">
                                Close Window
                            </button>
                            <p style="color: #6c757d; margin-top: 10px; font-size: 14px;">Window will close automatically in 3 seconds...</p>
                        </div>
                    `;
                    
                    // Auto-close after 3 seconds using the same code as the button
                    setTimeout(() => {{
                        window.close();
                    }}, 3000);
                }} else {{
                    // No token - show error
                    document.getElementById('status').innerHTML = `
                        <div>
                            <h2 style="color: #dc3545;">❌ Token Exchange Failed</h2>
                            <p>Authentication succeeded but automatic token exchange failed.</p>
                            <p>Authorization code: <code>${{authCode || "None"}}</code></p>
                            <p>Please try the manual process or contact support.</p>
                            <p style="margin-top: 30px;">
                                <a href="/docs" style="background: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px;">Return to API Docs</a>
                            </p>
                        </div>
                    `;
                }}
            }}
            
            if (document.readyState !== 'loading') {{
                run();
            }} else {{
                document.addEventListener('DOMContentLoaded', function () {{
                    run();
                }});
            }}
        </script>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html_content)

# Note: Custom OAuth2 login endpoint removed - now using standard Swagger UI OAuth2 flow


# Simple working docs endpoint (before route class restoration)  
@app.get("/docs", include_in_schema=False)
def custom_swagger_ui_html():
    """
    Custom Swagger UI docs 
    """
    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Morpheus API Gateway - API Documentation</title>
        <link type="text/css" rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@4/swagger-ui.css">
    </head>
    <body>
        <div id="swagger-ui"></div>
        <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@4/swagger-ui-bundle.js"></script>
        <script>
            const ui = SwaggerUIBundle({{
                url: '/api/v1/openapi.json',
                dom_id: '#swagger-ui',
                layout: 'BaseLayout',
                oauth2RedirectUrl: window.location.origin + '/docs/oauth2-redirect'
            }});
            
            // Make UI available globally for token application
            window.ui = ui;
            
            ui.initOAuth({{
                clientId: '{settings.COGNITO_CLIENT_ID}',
                realm: 'oauth2',
                appName: 'Morpheus API Gateway',
                scopeSeparator: ' ',
                scopes: 'openid email profile',
                usePkceWithAuthorizationCodeGrant: false,
                useBasicAuthenticationWithAccessCodeGrant: false,
                additionalQueryStringParams: {{
                    'response_type': 'code',
                    'state': 'swagger-ui-oauth2'
                }}
            }});
            
            // Debug: Log OAuth2 configuration
            console.log('🔍 OAuth2 redirect configured');
            
            // Override OAuth2 authorization to use popup instead of new tab
            setTimeout(() => {{
                console.log('🔍 Setting up OAuth2 popup override...');
                
                // Override the window.open function specifically for OAuth2 URLs
                const originalWindowOpen = window.open;
                window.open = function(url, target, features) {{
                    if (url && url.includes('/oauth2/authorize')) {{
                        console.log('🔍 OAuth2 authorization detected, opening popup instead of tab');
                        
                        // Set up Swagger UI OAuth2 redirect callback for popup detection
                        window.swaggerUIRedirectOauth2 = {{
                            auth: 'OAuth2',
                            redirectUrl: window.location.origin + '/docs/oauth2-redirect',
                            callback: function(data) {{
                                console.log('✅ OAuth2 popup callback received:', data);
                                if (data.token && data.token.access_token) {{
                                    console.log('✅ Applying token from popup callback...');
                                    try {{
                                        window.ui.preauthorizeApiKey('BearerAuth', data.token.access_token);
                                        console.log('✅ Bearer token applied successfully from popup!');
                                    }} catch (e) {{
                                        console.log('⚠️ Error applying token from popup:', e);
                                    }}
                                }}
                            }}
                        }};
                        
                        // Open popup with specific features
                        const popup = originalWindowOpen.call(
                            this, 
                            url, 
                            'oauth2_auth_popup',
                            'width=600,height=700,scrollbars=yes,resizable=yes,status=yes,location=yes,toolbar=no,menubar=no,left=' + 
                            Math.round((screen.width - 600) / 2) + ',top=' + Math.round((screen.height - 700) / 2)
                        );
                        
                        // Store popup reference globally for direct access
                        window.oauth2Popup = popup;
                        
                        // Monitor popup closure and token retrieval
                        const checkPopup = setInterval(() => {{
                            try {{
                                if (popup.closed) {{
                                    clearInterval(checkPopup);
                                    console.log('🔍 OAuth2 popup closed, checking for tokens...');
                                    
                                    // Check for token in localStorage with extended monitoring for new user flows
                                    setTimeout(() => {{
                                        const token = localStorage.getItem('swagger_oauth_token');
                                        if (token) {{
                                            console.log('✅ Token found from popup, applying to Bearer Auth...');
                                            try {{
                                                window.ui.preauthorizeApiKey('BearerAuth', token);
                                                console.log('✅ Bearer token applied successfully!');
                                            }} catch (e) {{
                                                console.log('⚠️ Error applying token:', e);
                                            }}
                                            localStorage.removeItem('swagger_oauth_token');
                                            localStorage.removeItem('swagger_oauth_token_timestamp');
                                        }} else {{
                                            // Extended monitoring for new user registration flows
                                            console.log('🔍 No token found immediately - starting extended monitoring for new user flows...');
                                            let extendedChecks = 0;
                                            const maxExtendedChecks = 10; // Check for 10 more seconds
                                            
                                            const extendedMonitor = setInterval(() => {{
                                                extendedChecks++;
                                                const delayedToken = localStorage.getItem('swagger_oauth_token');
                                                
                                                if (delayedToken) {{
                                                    console.log('✅ Token found during extended monitoring!');
                                                    clearInterval(extendedMonitor);
                                                    
                                                    // Use the same multi-method approach as page load
                                                    try {{
                                                        console.log('🔍 Attempting to authorize with delayed token...');
                                                        
                                                        if (window.ui) {{
                                                            // Method 1: Use preauthorizeApiKey for BearerAuth
                                                            try {{
                                                                window.ui.preauthorizeApiKey('BearerAuth', delayedToken);
                                                                console.log('✅ BearerAuth preauthorized from extended monitoring!');
                                                            }} catch (e) {{
                                                                console.log('⚠️ preauthorizeApiKey failed:', e);
                                                            }}
                                                            
                                                            // Method 2: Try the direct authActions approach
                                                            if (window.ui.authActions) {{
                                                                try {{
                                                                    window.ui.authActions.authorize({{
                                                                        'BearerAuth': delayedToken
                                                                    }});
                                                                    console.log('✅ BearerAuth via authActions from extended monitoring!');
                                                                }} catch (e) {{
                                                                    console.log('⚠️ authActions.authorize failed:', e);
                                                                }}
                                                            }}
                                                            
                                                            // Method 3: Safari-specific handling
                                                            if (navigator.userAgent.includes('Safari') && !navigator.userAgent.includes('Chrome')) {{
                                                                setTimeout(() => {{
                                                                    try {{
                                                                        window.ui.authActions.authorize({{
                                                                            'BearerAuth': {{
                                                                                value: delayedToken
                                                                            }}
                                                                        }});
                                                                        console.log('✅ Safari-specific auth from extended monitoring!');
                                                                    }} catch (e) {{
                                                                        console.log('⚠️ Safari auth failed:', e);
                                                                    }}
                                                                }}, 500);
                                                            }}
                                                        }}
                                                    }} catch (error) {{
                                                        console.error('❌ Error applying delayed token:', error);
                                                    }}
                                                    
                                                    localStorage.removeItem('swagger_oauth_token');
                                                    localStorage.removeItem('swagger_oauth_token_timestamp');
                                                }} else if (extendedChecks >= maxExtendedChecks) {{
                                                    console.log('⚠️ Extended monitoring timeout - no token found');
                                                    clearInterval(extendedMonitor);
                                                }}
                                            }}, 1000);
                                        }}
                                    }}, 500);
                                    return;
                                }}
                                
                                // Check for successful token every second
                                const token = localStorage.getItem('swagger_oauth_token');
                                if (token) {{
                                    console.log('✅ Token detected! Closing popup and applying token...');
                                    clearInterval(checkPopup);
                                    
                                    // Store token for Safari handling before cleanup
                                    const tokenForSafari = token;
                                    
                                    // Apply token immediately
                                    try {{
                                        window.ui.preauthorizeApiKey('BearerAuth', token);
                                        console.log('✅ Bearer token applied successfully!');
                                    }} catch (e) {{
                                        console.log('⚠️ Error applying token:', e);
                                    }}
                                    
                                    // Close popup explicitly
                                    if (!popup.closed) {{
                                        popup.close();
                                        console.log('✅ Popup closed successfully');
                                    }}
                                    
                                    // Clean up
                                    localStorage.removeItem('swagger_oauth_token');
                                    localStorage.removeItem('swagger_oauth_token_timestamp');
                                    delete window.oauth2Popup;
                                    
                                    // Safari-specific: Force a UI refresh to ensure token visibility
                                    if (navigator.userAgent.includes('Safari') && !navigator.userAgent.includes('Chrome')) {{
                                        console.log('🍎 Safari detected - forcing UI refresh...');
                                        setTimeout(() => {{
                                            try {{
                                                // Try multiple Safari-friendly approaches
                                                if (window.ui && window.ui.authActions) {{
                                                    window.ui.authActions.authorize({{
                                                        'BearerAuth': {{
                                                            value: tokenForSafari
                                                        }}
                                                    }});
                                                    console.log('✅ Safari UI refresh attempted');
                                                }}
                                            }} catch (e) {{
                                                console.log('⚠️ Safari refresh attempt failed:', e);
                                            }}
                                        }}, 500);
                                    }}
                                }}
                            }} catch (e) {{
                                // Cross-origin error - popup still open, continue monitoring
                            }}
                        }}, 1000);
                        
                        return popup;
                    }}
                    
                    // For all other URLs, use original window.open
                    return originalWindowOpen.call(this, url, target, features);
                }};
                
                console.log('✅ OAuth2 popup override installed');
            }}, 2000); // Wait for Swagger UI to fully initialize
            
            // Check for OAuth token in localStorage (from new tab flow)
            setTimeout(() => {{
                console.log('🔍 Checking for stored OAuth token...');
                const storedToken = localStorage.getItem('swagger_oauth_token');
                const tokenTimestamp = localStorage.getItem('swagger_oauth_token_timestamp');
                
                // Check token availability (reduced logging for production)
                console.log('🔍 Checking OAuth token status...');
                
                if (storedToken && tokenTimestamp) {{
                    const tokenAge = Date.now() - parseInt(tokenTimestamp);
                    const maxAge = 5 * 60 * 1000; // 5 minutes
                    
                    if (tokenAge < maxAge) {{
                        console.log('✅ Found stored OAuth token, applying automatically...');
                        
                        // Apply OAuth2 token to Swagger UI
                        try {{
                            console.log('🔍 Attempting to authorize with stored token...');
                            
                            if (window.ui) {{
                                // Method 1: Use preauthorizeApiKey for BearerAuth (this usually works)
                                try {{
                                    window.ui.preauthorizeApiKey('BearerAuth', storedToken);
                                    console.log('✅ BearerAuth preauthorized!');
                                }} catch (e) {{
                                    console.log('⚠️ preauthorizeApiKey failed:', e);
                                }}
                                
                                // Method 2: Try the direct authActions approach
                                if (window.ui.authActions) {{
                                    try {{
                                        window.ui.authActions.authorize({{
                                            'BearerAuth': storedToken
                                        }});
                                        console.log('✅ BearerAuth via authActions!');
                                    }} catch (e) {{
                                        console.log('⚠️ authActions.authorize failed:', e);
                                    }}
                                }}
                                
                                // Method 3: Try to set OAuth2 authorization
                                if (window.ui.authActions) {{
                                    try {{
                                        window.ui.authActions.authorize({{
                                            'OAuth2': {{
                                                token: {{
                                                    access_token: storedToken,
                                                    token_type: 'Bearer'
                                                }}
                                            }}
                                        }});
                                        console.log('✅ OAuth2 via authActions!');
                                    }} catch (e) {{
                                        console.log('⚠️ OAuth2 authActions failed:', e);
                                    }}
                                }}
                                
                                // Method 4: Direct state manipulation (last resort)
                                setTimeout(() => {{
                                    try {{
                                        const state = window.ui.getState();
                                        console.log('🔍 Current auth state:', state.getIn(['auth', 'authorized']));
                                        
                                        // Force update the auth state
                                        window.ui.authActions.authorizeWithPersistOption({{
                                            'BearerAuth': {{
                                                value: storedToken
                                            }}
                                        }});
                                        console.log('✅ State manipulation attempted!');
                                    }} catch (e) {{
                                        console.log('⚠️ State manipulation failed:', e);
                                    }}
                                }}, 1000);
                                
                            }} else {{
                                console.error('❌ Swagger UI not available');
                                alert('Authentication successful! Token: ' + storedToken.substring(0, 50) + '... Please manually paste in Bearer Auth field.');
                            }}
                        }} catch (error) {{
                            console.error('❌ Error applying token:', error);
                            alert('Authentication successful! Token: ' + storedToken.substring(0, 50) + '... Please manually paste in Bearer Auth field.');
                        }}
                        
                        // Clean up localStorage
                        localStorage.removeItem('swagger_oauth_token');
                        localStorage.removeItem('swagger_oauth_token_timestamp');
                    }} else {{
                        console.log('⚠️ Stored token expired, removing...');
                        localStorage.removeItem('swagger_oauth_token');
                        localStorage.removeItem('swagger_oauth_token_timestamp');
                    }}
                }}
            }}, 1000); // Wait for Swagger UI to fully initialize
        </script>
    </body>
    </html>
    """)

@app.get("/exchange-token", include_in_schema=False)
async def exchange_oauth_token(request: Request, code: str, state: str = None):
    """
    Exchange OAuth2 authorization code for access token
    """
    import httpx
    
    try:
        # Exchange the authorization code for tokens
        token_url = f"https://{settings.COGNITO_DOMAIN}/oauth2/token"
        
        data = {
            "grant_type": "authorization_code",
            "client_id": settings.COGNITO_CLIENT_ID,
            "code": code,
            "redirect_uri": f"{settings.BASE_URL}/docs/oauth2-redirect"
        }
        
        # Add PKCE code_verifier if provided
        code_verifier = request.query_params.get("code_verifier")
        if code_verifier:
            data["code_verifier"] = code_verifier
        
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(token_url, data=data, headers=headers)
            
        if response.status_code == 200:
            tokens = response.json()
            return {
                "success": True,
                "access_token": tokens.get("access_token"),
                "token_type": tokens.get("token_type"),
                "expires_in": tokens.get("expires_in"),
                "id_token": tokens.get("id_token"),
                "message": "✅ Use the 'access_token' as your Bearer token in Swagger UI!"
            }
        else:
            return {
                "success": False,
                "error": response.text,
                "status_code": response.status_code
            }
            
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

# OAuth helper endpoint removed for security - no longer exposing client_id in helper tools

# Debug endpoint removed for security - no longer exposing sensitive OAuth configuration

# Restore the original route class for subsequent routes
app.router.route_class = original_route_class

# Note: Routes defined after route class restoration don't work properly

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
    
    # Ensure OpenAPI version is set
    openapi_schema["openapi"] = "3.0.2"
    
    # Ensure servers are included in the schema
    openapi_schema["servers"] = app.servers

    # Add custom info about authentication
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
    
    # Note: Component schemas are automatically generated by FastAPI
    
    # Add OAuth2 security scheme for standard Swagger UI authorization
    openapi_schema["components"]["securitySchemes"] = {
        "OAuth2": {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": f"https://{settings.COGNITO_DOMAIN}/oauth2/authorize",
                    "tokenUrl": f"https://{settings.COGNITO_DOMAIN}/oauth2/token",
                    "scopes": {
                        "openid": "OpenID Connect authentication",
                        "email": "Access to email address", 
                        "profile": "Access to profile information"
                    }
                }
            },
            "description": "🚀 OAuth2 authentication via secure identity provider"
        },
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "🎫 JWT Bearer token from OAuth2 login or direct token"
        },
        "APIKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "🗝️ API key in format: 'Bearer sk-xxxxxx'"
        }
    }
    
    # Apply security to all API endpoints (except excluded ones)
    for path_key, path_item in openapi_schema["paths"].items():
        # Skip certain endpoints that should remain unauthenticated
        if path_key in ["/", "/health", "/docs", "/api-docs"] or path_key.startswith("/docs/"):
            continue
            
        # Apply all authentication methods to API endpoints
        for method, operation in path_item.items():
            if method in ["get", "post", "put", "delete", "patch"]:
                operation["security"] = [
                    {"OAuth2": ["openid", "email", "profile"]},
                    {"BearerAuth": []},
                    {"APIKeyAuth": []}
                ]

    app.openapi_schema = openapi_schema
    return app.openapi_schema

# Set custom OpenAPI schema generator
app.openapi = custom_openapi

# Create custom OpenAPI endpoint to ensure our OAuth2 schema is used
@app.get(f"{settings.API_V1_STR}/openapi.json", include_in_schema=False)
async def get_custom_openapi():
    """
    Custom OpenAPI endpoint that ensures our OAuth2 security scheme is included
    """
    return custom_openapi() 

# API Documentation landing page
@app.get("/api-docs", include_in_schema=False)
async def api_docs_landing(request: Request):
    """
    Landing page for API docs
    """
    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{app.title} - Documentation</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
            .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 40px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            h1 {{ color: #333; margin-bottom: 20px; }}
            .api-link {{ display: inline-block; background: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; margin: 10px 10px 10px 0; }}
            .api-link:hover {{ background: #0056b3; }}
            .description {{ margin: 20px 0; line-height: 1.6; color: #666; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 {app.title}</h1>
            <p class="description">
                Welcome to the Morpheus API Gateway documentation. 
                Choose your preferred documentation format below:
            </p>
            
            <a href="/docs" class="api-link">📋 Interactive API Docs (Swagger UI)</a>
            <a href="/redoc" class="api-link">📖 API Documentation (ReDoc)</a>
            
            <div class="description">
                <h3>🔐 Authentication Methods</h3>
                <ul>
                    <li><strong>OAuth2:</strong> Login with your account credentials for the easiest experience</li>
                    <li><strong>JWT Bearer Token:</strong> Use access tokens from successful OAuth2 logins</li>
                    <li><strong>API Keys:</strong> Programmatic access using generated API keys</li>
                </ul>
                
                <h3>📚 Key Features</h3>
                <ul>
                    <li>OpenAI-compatible chat completions endpoint</li>
                    <li>Model discovery and management</li>
                    <li>Session management for blockchain interactions</li>
                    <li>Comprehensive authentication and authorization</li>
                </ul>
                
                <p style="margin-top: 30px; font-size: 12px; color: #999;">
                    © {datetime.now().year} Morpheus API Gateway
                </p>
            </div>
        </div>
    </body>
    </html>
    """) 