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

from src.api.v1 import models, chat, session, auth, automation
from src.core.config import settings
from src.api.v1.custom_route import FixedDependencyAPIRoute
from src.db.models import Session as DbSession
from src.services import session_service
from src.db.database import engine, get_db
from src.core.model_sync import model_sync_service

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
    version=f"0.2.0-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
    redirect_slashes=False,  # Disable automatic redirects to prevent HTTPS→HTTP downgrade attacks
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=None,  # Disable default docs so we can customize it
    redoc_url="/redoc",  # Re-enable ReDoc for alternative documentation
    swagger_ui_oauth2_redirect_url="/docs/oauth2-redirect"
)

# Set our fixed dependency route class for all APIRouters
app.router.route_class = FixedDependencyAPIRoute

# Note: Custom OpenAPI function is defined later in the file

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
    logger.info("🔄 Starting Morpheus API Gateway startup sequence...")
    logger.info(f"📊 Configuration: MODEL_SYNC_ON_STARTUP={settings.MODEL_SYNC_ON_STARTUP}, MODEL_SYNC_ENABLED={settings.MODEL_SYNC_ENABLED}")
    
    # Verify database migrations are up to date
    logger.info("🗃️ Checking database migrations...")
    await verify_database_migrations()
    
    # Sync models on startup if enabled
    logger.info("🤖 Initializing model synchronization...")
    if settings.MODEL_SYNC_ON_STARTUP and settings.MODEL_SYNC_ENABLED:
        logger.info("📥 Starting model synchronization from active.mor.org...")
        try:
            sync_success = await model_sync_service.perform_sync()
            if sync_success:
                logger.info("✅ Model sync completed successfully during startup")
            else:
                logger.warning("⚠️ Model sync failed during startup, but continuing with existing models")
        except Exception as e:
            logger.error(f"❌ Model sync failed during startup: {e}")
            logger.warning("Continuing startup with existing models.json file")
    else:
        logger.info("📴 Model sync on startup is disabled")
    
    # Start background model sync task if enabled
    if settings.MODEL_SYNC_ENABLED:
        try:
            await model_sync_service.start_background_sync()
            logger.info("✅ Background model sync started successfully")
        except Exception as e:
            logger.error(f"❌ Failed to start background model sync: {e}")
            logger.warning("Continuing startup without background model sync...")
    else:
        logger.info("Background model sync is disabled")
    
    # Make sure all routers use our fixed route class
    try:
        for router in [auth, models, chat, session, automation]:
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
    
    # Stop the background model sync task
    try:
        await model_sync_service.stop_background_sync()
        logger.info("✅ Background model sync stopped successfully")
    except Exception as e:
        logger.error(f"❌ Error stopping background model sync: {e}")
    
    logger.info("🏁 Application shutdown complete")

async def verify_database_migrations():
    """
    Verify that database migrations are up to date.
    """
    try:
        logger.info("Starting database migration check")
        
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
        
        # Get the current head revision from the script directory
        head_revision = script_dir.get_current_head()
        logger.info(f"Latest migration head: {head_revision}")
        
        # Connect to database and check current revision
        async with engine.begin() as conn:
            # Check if alembic_version table exists
            result = await conn.execute(text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name='alembic_version')"
            ))
            table_exists = result.scalar()
            
            if not table_exists:
                logger.warning("Alembic version table doesn't exist - database may need initialization")
                return
            
            # Get current database revision
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            current_revision = result.scalar()
            
            if current_revision is None:
                logger.warning("No migration version found in database")
                return
                
            logger.info(f"Current database revision: {current_revision}")
            
            # Compare revisions
            if current_revision == head_revision:
                logger.info("✅ Database migrations are up to date")
            else:
                logger.warning(f"⚠️ Database migration mismatch - DB: {current_revision}, Latest: {head_revision}")
                logger.info("Database may need migration, but continuing startup...")
                
    except Exception as e:
        logger.error(f"Error checking migrations: {str(e)}")
        logger.warning("Migration check failed, but continuing startup...")
        # Don't raise the exception to prevent startup failure
    finally:
        logger.info("Migration check completed")

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
app.include_router(models, prefix=f"{settings.API_V1_STR}")  # Mount at /api/v1 and let models handle /models
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
        "version": "0.2.0",
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
        "version": "0.2.0",
        "database": db_status
    }

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
                        
                        document.getElementById('status').innerHTML = '✅ Authentication complete! Closing window...';
                        setTimeout(() => window.close(), 1000);
                        return;
                    }} catch (e) {{
                        console.error('❌ Popup callback error:', e);
                    }}
                }}
                
                // Handle as new tab
                console.log('🔄 Handling as new tab scenario');
                
                if (accessToken) {{
                    // Store token in localStorage and redirect back to docs
                    console.log('✅ Storing token in localStorage and redirecting...');
                    localStorage.setItem('swagger_oauth_token', accessToken);
                    localStorage.setItem('swagger_oauth_token_timestamp', Date.now().toString());
                    
                    document.getElementById('status').innerHTML = `
                        <div>
                            <h2 style="color: #28a745;">✅ Authentication Successful!</h2>
                            <p>🔄 Redirecting you back to API documentation...</p>
                        </div>
                    `;
                    
                    // Redirect back to docs page after a short delay
                    setTimeout(() => {{
                        window.location.href = '/docs';
                    }}, 1500);
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
                realm: 'cognito',
                appName: 'Morpheus API Gateway',
                scopeSeparator: ' ',
                scopes: 'openid email profile',
                usePkceWithAuthorizationCodeGrant: false,
                useBasicAuthenticationWithAccessCodeGrant: false,
                additionalQueryStringParams: {{}}
            }});
            
            // Debug: Log OAuth2 configuration
            console.log('🔍 OAuth2 redirect configured');
            
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

@app.get("/oauth-helper", include_in_schema=False)
async def oauth_helper():
    """
    Generate proper OAuth2 URLs with PKCE for manual testing
    """
    import secrets
    import hashlib
    import base64
    from urllib.parse import urlencode
    
    # Generate PKCE values
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('utf-8')).digest()
    ).decode('utf-8').rstrip('=')
    
    # Build OAuth2 URLs
    base_url = "https://auth.mor.org/oauth2/authorize"
    redirect_uri = f"{settings.BASE_URL}/docs/oauth2-redirect"
    
    # Simple URL (no PKCE)
    simple_params = {
        "client_id": settings.COGNITO_CLIENT_ID,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": redirect_uri,
        "state": "manual-test"
    }
    simple_url = f"{base_url}?{urlencode(simple_params)}"
    
    # PKCE URL
    pkce_params = {
        "client_id": settings.COGNITO_CLIENT_ID,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": redirect_uri,
        "state": "manual-test-pkce",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256"
    }
    pkce_url = f"{base_url}?{urlencode(pkce_params)}"
    
    return {
        "instructions": "Try the simple URL first. If it requires PKCE, use the PKCE URL.",
        "simple_url": simple_url,
        "pkce_url": pkce_url,
        "pkce_values": {
            "code_verifier": code_verifier,
            "code_challenge": code_challenge
        },
        "exchange_endpoint": f"{settings.BASE_URL}/exchange-token?code=YOUR_CODE&code_verifier={code_verifier}"
    }

@app.get("/debug/oauth-config", include_in_schema=False)
async def debug_oauth_config():
    """
    Debug endpoint to see the actual OAuth2 configuration being generated.
    Remove this in production.
    """
    client_id = settings.COGNITO_CLIENT_ID
    
    init_oauth_config = f'''{{
        clientId: '{client_id}',
        realm: 'cognito',
        appName: 'Morpheus API Gateway',
        scopeSeparator: ' ',
        scopes: 'openid email profile',
        usePkceWithAuthorizationCodeGrant: false,
        useBasicAuthenticationWithAccessCodeGrant: false,
        additionalQueryStringParams: {{
            'response_type': 'code',
            'state': 'swagger-ui-oauth2'
        }}
    }}'''
    
    # Test custom OpenAPI function
    try:
        openapi_schema = custom_openapi()
        oauth2_scheme = openapi_schema.get("components", {}).get("securitySchemes", {}).get("OAuth2")
        
        return {
            "client_id": client_id,
            "cognito_domain": settings.COGNITO_DOMAIN,
            "init_oauth_config": init_oauth_config,
            "javascript_snippet": f"initOAuth: {init_oauth_config}",
            "custom_openapi_working": bool(oauth2_scheme),
            "oauth2_scheme": oauth2_scheme,
            "openapi_url": f"{settings.API_V1_STR}/openapi.json",
            "status": "client_id should be pre-filled in OAuth2 modal"
        }
    except Exception as e:
        return {
            "client_id": client_id,
            "cognito_domain": settings.COGNITO_DOMAIN,
            "error": str(e),
            "openapi_url": f"{settings.API_V1_STR}/openapi.json",
            "status": "Error in custom_openapi function"
        }

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
            "description": "🚀 OAuth2 authentication via Cognito"
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
                    <li><strong>OAuth2:</strong> Login with your Cognito credentials for the easiest experience</li>
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