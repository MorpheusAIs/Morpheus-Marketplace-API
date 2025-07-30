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
    docs_url=None,  # Disable automatic /docs endpoint so our custom one works
    redoc_url=None,  # Also disable /redoc to avoid confusion
    swagger_ui_oauth2_redirect_url="/docs/oauth2-redirect"
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
    # Get authorization code from query parameters
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    
    if error:
        return HTMLResponse(content=f"""
        <!DOCTYPE html>
        <html>
        <head><title>OAuth2 Error</title></head>
        <body>
            <h1>OAuth2 Authentication Error</h1>
            <p>Error: {error}</p>
            <p>Description: {request.query_params.get("error_description", "Unknown error")}</p>
            <p><a href="/docs">Return to API Documentation</a></p>
        </body>
        </html>
        """)
    
    if not code:
        return HTMLResponse(content="""
        <!DOCTYPE html>
        <html>
        <head><title>OAuth2 Processing</title></head>
        <body>
            <h1>OAuth2 Processing</h1>
            <p>Processing OAuth2 callback...</p>
            <script>
                // Extract token if present in URL fragment (implicit flow)
                const fragment = window.location.hash.substring(1);
                const params = new URLSearchParams(fragment);
                const accessToken = params.get('access_token');
                
                if (accessToken) {
                    // Store token and redirect to docs
                    sessionStorage.setItem('oauth_access_token', accessToken);
                    window.location.href = '/docs?oauth_success=true';
                } else {
                    // No code or token found
                    document.body.innerHTML = '<h1>Error</h1><p>No authorization code or token received.</p><p><a href="/docs">Return to API Documentation</a></p>';
                }
            </script>
        </body>
        </html>
        """)
    
    # We have an authorization code - exchange it for tokens
    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Completing OAuth2 Login...</title>
        <style>
            body {{ font-family: Arial, sans-serif; padding: 40px; text-align: center; }}
            .spinner {{ border: 4px solid #f3f3f3; border-top: 4px solid #3498db; border-radius: 50%; width: 40px; height: 40px; animation: spin 2s linear infinite; margin: 20px auto; }}
            @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
        </style>
    </head>
    <body>
        <h1>🔄 Completing OAuth2 Login...</h1>
        <div class="spinner"></div>
        <p>Exchanging authorization code for access token...</p>
        
        <script>
            async function exchangeCodeForToken() {{
                try {{
                    const code = '{code}';
                    const clientId = '{settings.COGNITO_CLIENT_ID}';
                    // Use the same redirect_uri that was used in the authorization request
                    const redirectUri = 'https://' + window.location.host + '/docs/oauth2-redirect';
                    
                    console.log('🔄 Exchanging code for token...', {{ code: code.substring(0, 10) + '...' }});
                    
                    // Get the code_verifier from the cookie that was set during oauth-login
                    function getCookie(name) {{
                        const value = `; ${{document.cookie}}`;
                        const parts = value.split(`; ${{name}}=`);
                        if (parts.length === 2) return parts.pop().split(';').shift();
                        return null;
                    }}
                    
                    const codeVerifier = getCookie('oauth_code_verifier');
                    console.log('🔑 Code verifier found:', codeVerifier ? 'Yes' : 'No');
                    
                    // Validate state parameter for CSRF protection
                    const urlParams = new URLSearchParams(window.location.search);
                    const receivedState = urlParams.get('state');
                    const expectedState = getCookie('oauth_state');
                    
                    console.log('🛡️ State validation:', {{ 
                        received: receivedState ? receivedState.substring(0, 10) + '...' : 'none',
                        expected: expectedState ? expectedState.substring(0, 10) + '...' : 'none',
                        valid: receivedState === expectedState
                    }});
                    
                    if (!expectedState || receivedState !== expectedState) {{
                        throw new Error('State validation failed - possible CSRF attack');
                    }}
                    
                    // Prepare token exchange parameters
                    const tokenParams = {{
                        grant_type: 'authorization_code',
                        client_id: clientId,
                        code: code,
                        redirect_uri: redirectUri
                    }};
                    
                    // Add PKCE code_verifier if available
                    if (codeVerifier) {{
                        tokenParams.code_verifier = codeVerifier;
                    }}
                    
                    console.log('📤 Token exchange params:', {{ ...tokenParams, code: code.substring(0, 10) + '...', code_verifier: codeVerifier ? codeVerifier.substring(0, 10) + '...' : 'none' }});
                    
                    // Exchange authorization code for access token
                    const tokenResponse = await fetch('https://{settings.COGNITO_DOMAIN}/oauth2/token', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/x-www-form-urlencoded',
                        }},
                        body: new URLSearchParams(tokenParams)
                    }});
                    
                    console.log('📥 Token response status:', tokenResponse.status);
                    
                    if (!tokenResponse.ok) {{
                        const errorText = await tokenResponse.text();
                        console.error('❌ Token exchange error:', errorText);
                        throw new Error(`Token exchange failed: ${{tokenResponse.status}} ${{tokenResponse.statusText}} - ${{errorText}}`);
                    }}
                    
                    const tokenData = await tokenResponse.json();
                    console.log('✅ Token exchange successful!');
                    
                    // Store the access token
                    sessionStorage.setItem('oauth_access_token', tokenData.access_token);
                    sessionStorage.setItem('oauth_token_type', tokenData.token_type || 'Bearer');
                    if (tokenData.refresh_token) {{
                        sessionStorage.setItem('oauth_refresh_token', tokenData.refresh_token);
                    }}
                    
                    // Redirect back to docs with success flag
                    window.location.href = '/docs?oauth_success=true';
                    
                }} catch (error) {{
                    console.error('❌ OAuth2 token exchange failed:', error);
                    document.body.innerHTML = `
                        <h1>❌ Authentication Failed</h1>
                        <p>Failed to complete OAuth2 login: ${{error.message}}</p>
                        <p><a href="/docs">Return to API Documentation</a></p>
                    `;
                }}
            }}
            
            // Start the token exchange
            exchangeCodeForToken();
        </script>
    </body>
    </html>
    """)

# OAuth2 login endpoint  
@app.get("/oauth-login", include_in_schema=False)
async def oauth_login(request: Request):
    """Direct OAuth2 login endpoint"""
    # Generate PKCE parameters
    import secrets
    import base64
    import hashlib
    
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('utf-8')).digest()
    ).decode('utf-8').rstrip('=')
    
    state = secrets.token_urlsafe(32)
    
    # Store PKCE parameters in the redirect URL (they'll be handled by the OAuth2 redirect endpoint)
    # Force HTTPS for redirect_uri since load balancer terminates SSL
    redirect_uri = f"https://{request.url.netloc}/docs/oauth2-redirect"
    
    auth_url = (
        f"https://{settings.COGNITO_DOMAIN}/oauth2/authorize?"
        f"response_type=code&"
        f"client_id={settings.COGNITO_CLIENT_ID}&"
        f"redirect_uri={quote(redirect_uri)}&"
        f"scope=openid+email+profile&"
        f"state={state}&"
        f"code_challenge={code_challenge}&"
        f"code_challenge_method=S256"
    )
    
    # Store code_verifier in session/cookie for the callback
    response = RedirectResponse(auth_url)
    response.set_cookie("oauth_code_verifier", code_verifier, httponly=False, max_age=600, secure=True, samesite="Lax")
    response.set_cookie("oauth_state", state, httponly=False, max_age=600, secure=True, samesite="Lax")
    
    return response


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    """Custom Swagger UI with OAuth2 login button"""
    
    # Get the current build version
    build_version = f"0.1.0-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    
    # Create custom HTML with embedded OAuth panel
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Morpheus API Gateway - API Documentation</title>
        <link type="text/css" rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@4/swagger-ui.css">
        <link rel="shortcut icon" href="https://fastapi.tiangolo.com/img/favicon.png">
        <style>
            .oauth-panel {{
                background: linear-gradient(135deg, #d1ecf1 0%, #bee5eb 100%);
                padding: 20px;
                margin: 20px 0;
                border-radius: 8px;
                position: relative;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
            .build-version {{
                position: absolute;
                top: 5px;
                right: 10px;
                font-size: 11px;
                color: #6c757d;
                background: rgba(255,255,255,0.8);
                padding: 2px 6px;
                border-radius: 3px;
                font-family: monospace;
            }}
            .oauth-btn {{
                background: linear-gradient(135deg, #28a745, #20c997);
                color: white;
                border: none;
                padding: 12px 24px;
                border-radius: 25px;
                font-weight: bold;
                text-decoration: none;
                display: inline-block;
                margin-right: 10px;
                transition: all 0.3s ease;
            }}
            .jwt-btn {{
                background: linear-gradient(135deg, #007bff, #6610f2);
                color: white;
                border: none;
                padding: 12px 24px;
                border-radius: 25px;
                font-weight: bold;
                cursor: pointer;
                transition: all 0.3s ease;
            }}
            /* Hide the default Swagger UI authorize button */
            .swagger-ui .auth-wrapper .authorize {{
                display: none !important;
            }}
            .swagger-ui .btn.authorize {{
                display: none !important;
            }}
        </style>
    </head>
    <body>
        <div id="oauth-panel" class="oauth-panel">
            <div style="margin-bottom: 15px;">
                <a href="/oauth-login" class="oauth-btn">🚀 User Registration / Login with OAuth2</a>
                <button onclick="openAuthModal()" class="jwt-btn">🎫 Use JWT/API Key</button>
            </div>
        </div>
        
        <div id="swagger-ui"></div>
        
        <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@4/swagger-ui-bundle.js"></script>
        <script>
            console.log('🔧 Loading Swagger UI...');
            
            // Force Swagger UI to use current server URL
            const currentServerUrl = window.location.origin;
            console.log('🌍 Current server URL:', currentServerUrl);
            
            const ui = SwaggerUIBundle({{
                url: '/api/v1/openapi.json',
                dom_id: '#swagger-ui',
                layout: 'BaseLayout',
                deepLinking: true,
                persistAuthorization: true,
                displayRequestDuration: true,
                docExpansion: 'list',
                filter: true,
                tryItOutEnabled: true,
                oauth2RedirectUrl: '/docs/oauth2-redirect',
                // Force Swagger UI to use current host for API calls
                servers: [
                    {{ url: currentServerUrl, description: 'Current Server' }}
                ]
            }});
            
            console.log('✅ Swagger UI initialized with OAuth panel!');
            
            // Check for stored OAuth token and configure Swagger UI automatically
            function configureStoredAuth() {{
                // Check if we have a stored OAuth token
                const storedToken = sessionStorage.getItem('oauth_access_token');
                const tokenType = sessionStorage.getItem('oauth_token_type') || 'Bearer';
                
                if (storedToken) {{
                    console.log('🎫 Found stored OAuth token, configuring Swagger UI...');
                    
                    // Configure Bearer token authorization in Swagger UI
                    ui.preauthorizeApiKey('BearerAuth', storedToken);
                    ui.preauthorizeApiKey('APIKeyAuth', `Bearer ${{storedToken}}`);
                    
                    // Try additional auth configuration methods
                    try {{
                        if (ui.authActions && ui.authActions.authorize) {{
                            ui.authActions.authorize({{
                                'BearerAuth': storedToken,
                                'APIKeyAuth': `Bearer ${{storedToken}}`
                            }});
                        }}
                        console.log('🔧 Auth configuration applied');
                    }} catch (authError) {{
                        console.log('🔧 Additional auth method failed:', authError.message);
                    }}
                    

                     
                    // Force authorization header on all requests as backup + handle logout after deletion
                    const originalFetch = window.fetch;
                    window.fetch = function(url, options = {{}}) {{
                        console.log('🌐 Intercepted fetch to:', url, 'options:', options);
                        
                        // Only modify requests to our API
                        if (url.startsWith('/api/')) {{
                            options.headers = options.headers || {{}};
                            
                            // Log current headers
                            console.log('📋 Current headers before modification:', options.headers);
                            
                            // Always add/override auth header for API calls
                            const currentToken = sessionStorage.getItem('oauth_access_token');
                            if (currentToken) {{
                                options.headers.Authorization = `Bearer ${{currentToken}}`;
                                console.log('🔧 Force-added auth header to:', url, '- Token:', currentToken.substring(0, 20) + '...');
                            }} else {{
                                console.error('❌ No token in sessionStorage for:', url);
                            }}
                            
                            console.log('📋 Final headers:', options.headers);
                        }}
                        
                        // Call original fetch and handle the response
                        return originalFetch(url, options).then(response => {{
                            // Check if this was a successful account deletion
                            if (url.includes('/api/v1/auth/register') && options.method === 'DELETE' && response.ok) {{
                                console.log('🗑️ Account deletion successful - logging out user...');
                                
                                // Clear all authentication data
                                sessionStorage.removeItem('oauth_access_token');
                                sessionStorage.removeItem('oauth_token_type');
                                sessionStorage.removeItem('oauth_expires_in');
                                
                                // Clear authentication cookies
                                document.cookie = 'oauth_code_verifier=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
                                document.cookie = 'oauth_state=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
                                
                                // Clear any Swagger UI authentication
                                if (window.ui && window.ui.preauthorizeApiKey) {{
                                    window.ui.preauthorizeApiKey('BearerAuth', '');
                                    window.ui.preauthorizeApiKey('APIKeyAuth', '');
                                }}
                                
                                // Show success message and redirect after delay
                                setTimeout(() => {{
                                    alert('Account successfully deleted. You will be redirected to refresh the page.');
                                    window.location.reload(); // Reload the page to clear authentication state
                                }}, 2000); // 2 second delay to let the user see the success response
                            }}
                            
                            return response;
                        }}).catch(error => {{
                            // Handle any fetch errors
                            console.error('Fetch error:', error);
                            throw error;
                        }});
                    }};
                    
                    console.log('🔧 Set authorization in Swagger UI:', {{ 
                        tokenPreview: storedToken.substring(0, 20) + '...',
                        tokenType: tokenType,
                        method: 'preauthorizeApiKey + fetch override'
                    }});
                    

                    
                    // Update the OAuth panel to show authenticated status
                    const oauthPanel = document.getElementById('oauth-panel');
                    if (oauthPanel) {{
                        oauthPanel.innerHTML = `
                            <div class="build-version">Build: {build_version}</div>
                            <h4 style="color: #155724; margin-top: 0;">✅ Successfully Authenticated</h4>
                            <p style="margin-bottom: 15px; color: #155724;">You are logged in via OAuth2. All API calls will use your authenticated session.</p>
                            <div style="margin-bottom: 15px;">
                                <button onclick="logout()" style="background: #dc3545; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; margin-right: 10px;">🚪 Logout</button>
                                <span style="font-size: 12px; color: #6c757d;">Token: ${{storedToken.substring(0, 20)}}...</span>
                            </div>
                            <div style="margin-top: 10px; font-size: 14px; color: #6c757d;">
                                🎯 Ready to make authenticated API calls!
                            </div>
                        `;
                    }}
                    
                    console.log('✅ Swagger UI configured with stored token!');
                }} else {{
                    console.log('🔓 No stored token found - user needs to authenticate');
                }}
            }}
            
            // Logout function (make it globally accessible)
            window.logout = function() {{
                sessionStorage.removeItem('oauth_access_token');
                sessionStorage.removeItem('oauth_token_type');
                sessionStorage.removeItem('oauth_refresh_token');
                console.log('🚪 Logged out - clearing stored tokens');
                window.location.reload();
            }}
            
            // Function to open the auth modal (simplified)
            window.openAuthModal = function() {{
                console.log('🔑 Opening authentication modal...');
                
                // Simple method: find and click authorize button
                const authorizeBtn = document.querySelector('.btn.authorize') || 
                                   document.querySelector('.authorize');
                
                if (authorizeBtn) {{
                    authorizeBtn.style.display = 'block';
                    authorizeBtn.click();
                    setTimeout(function() {{ authorizeBtn.style.display = 'none'; }}, 100);
                    console.log('✅ Auth modal opened');
                }} else {{
                    alert('Please use the lock icons next to individual endpoints to authenticate.');
                    console.log('❌ Auth button not found');
                }}
            }}
            

            
            // Configure auth when UI is ready
            setTimeout(configureStoredAuth, 1000);
        </script>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html_content)

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
    
    # Ensure OpenAPI version is set
    openapi_schema["openapi"] = "3.0.2"
    
    # Ensure servers are included in the schema
    openapi_schema["servers"] = app.servers

    # Add custom info about authentication
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
    
    # Note: Component schemas are automatically generated by FastAPI
    
    # Only include manual authentication methods for the modal
    # OAuth2 is handled by the custom green button, not the modal
    openapi_schema["components"]["securitySchemes"] = {
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
            
        # Apply manual authentication methods to API endpoints
        # OAuth2 is handled by custom flow, not OpenAPI security
        for method, operation in path_item.items():
            if method in ["get", "post", "put", "delete", "patch"]:
                operation["security"] = [
                    {"BearerAuth": []},
                    {"APIKeyAuth": []}
                ]

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