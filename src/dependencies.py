from typing import Annotated, Optional
from dataclasses import dataclass
from uuid import UUID
import asyncio
from datetime import datetime

from fastapi import Depends, HTTPException, status, Security
from fastapi.security import HTTPBearer, APIKeyHeader, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import select
from sqlalchemy.future import select as future_select
import time
from datetime import datetime, timedelta
import boto3
from jose import jwt, jwk
from jose.utils import base64url_decode
import httpx

from src.core.config import settings
from src.core.security import verify_api_key
from src.crud import user as user_crud
from src.crud import api_key as api_key_crud
from src.db.database import get_db, get_db_session
from src.db.models import User, APIKey
from src.schemas.token import TokenPayload
from src.services.cognito_service import cognito_service
from src.services.cache_service import cache_service
from src.core.logging_config import get_auth_logger

auth_logger = get_auth_logger()

# Define bearer token scheme for JWT authentication
oauth2_scheme = HTTPBearer(
    auto_error=True,
    description="JWT Bearer token authentication"
)

# Define optional bearer token scheme for local testing
oauth2_scheme_optional = HTTPBearer(
    auto_error=False,
    description="JWT Bearer token authentication (optional for local testing)"
)

# Define API key scheme for API key authentication  
api_key_header = APIKeyHeader(
    name="Authorization", 
    auto_error=False,
    description="Provide the API key as 'Bearer sk-xxxxxx'"
)

cognito_client = boto3.client('cognito-idp', region_name=settings.AWS_REGION)

async def get_current_user(
    db: AsyncSession = Depends(get_db_session),
    token: Optional[HTTPAuthorizationCredentials] = Depends(oauth2_scheme_optional)
) -> User:
    """
    Validate Cognito JWT token and return the associated user.
    Creates user record if first time login.
    
    In local testing mode, bypasses Cognito and returns test user.
    """
    # Local testing bypass
    from src.core.local_testing import is_local_testing_mode, get_or_create_test_user
    if is_local_testing_mode():
        auth_logger.info("Using local testing mode - bypassing Cognito authentication",
                        event_type="local_testing_bypass")
        return await get_or_create_test_user(db)
    
    # Check if token is provided
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # Add debug logging for JWT validation
        auth_logger.debug("Starting JWT validation",
                         token_preview=token.credentials[:20],
                         expected_audience=settings.COGNITO_CLIENT_ID,
                         expected_issuer=f"https://cognito-idp.{settings.COGNITO_REGION}.amazonaws.com/{settings.COGNITO_USER_POOL_ID}",
                         event_type="jwt_validation_start")
        
        # Fetch JWKS from Cognito (with caching)
        jwks_url = settings.COGNITO_JWKS_URL
        
        # Try cache first
        cached_jwks = await cache_service.get("jwks", "cognito")
        if cached_jwks:
            auth_logger.debug("Using cached JWKS", event_type="jwks_cache_hit")
            jwks = cached_jwks
        else:
            # Cache miss - fetch from Cognito (async to avoid blocking the event loop)
            auth_logger.debug("Fetching JWKS from Cognito", jwks_url=jwks_url)
            async with httpx.AsyncClient(timeout=10.0) as client:
                jwks_response = await client.get(jwks_url)
                jwks_response.raise_for_status()
                jwks = jwks_response.json()
            
            # Cache JWKS for 15 minutes — shorter than access token TTL to avoid
            # the JWKS fetch coinciding with token expiry at the 60-minute boundary
            await cache_service.set("jwks", "cognito", jwks, ttl_seconds=900)
            auth_logger.debug("Cached JWKS", event_type="jwks_cached")
        
        # Get the key ID from token header
        header = jwt.get_unverified_header(token.credentials)
        auth_logger.debug("Token header retrieved", header=header)
        kid = header.get('kid')
        if not kid:
            auth_logger.error("No 'kid' found in token header", 
                             event_type="jwt_validation_error")
            raise credentials_exception
            
        # Find the matching key
        key = None
        for k in jwks['keys']:
            if k['kid'] == kid:
                key = k
                break
                
        if not key:
            raise credentials_exception
            
        # Construct the public key
        public_key = jwk.construct(key)
        
        # Decode and validate the token
        # Note: Cognito access tokens don't have 'aud' field, they have 'client_id'
        payload = jwt.decode(
            token.credentials, 
            public_key, 
            algorithms=['RS256'], 
            # audience=settings.COGNITO_CLIENT_ID,  # Skip audience validation
            issuer=f'https://cognito-idp.{settings.COGNITO_REGION}.amazonaws.com/{settings.COGNITO_USER_POOL_ID}'
        )
        
        # Manually validate client_id since Cognito uses that instead of audience
        token_client_id = payload.get('client_id')
        if token_client_id != settings.COGNITO_CLIENT_ID:
            auth_logger.error("Client ID mismatch",
                             expected=settings.COGNITO_CLIENT_ID,
                             received=token_client_id,
                             event_type="jwt_validation_error")
            raise credentials_exception
        
        auth_logger.info("JWT decode successful",
                        subject=payload.get('sub'),
                        email=payload.get('email'),
                        event_type="jwt_validation_success")
        
        # Extract user information from token
        cognito_user_id = payload.get('sub')
        token_email = payload.get('email')
        
        if not cognito_user_id:
            auth_logger.error("Missing cognito_user_id (sub) in token payload",
                             event_type="jwt_validation_error")
            raise credentials_exception
            
        # Try to get user from cache first
        cached_user = await cache_service.get("user", cognito_user_id)
        if cached_user:
            auth_logger.debug("Using cached user", cognito_user_id=cognito_user_id[:8] + "...")
            # Deserialize datetime fields
            if cached_user.get("created_at"):
                cached_user["created_at"] = datetime.fromisoformat(cached_user["created_at"])
            if cached_user.get("updated_at"):
                cached_user["updated_at"] = datetime.fromisoformat(cached_user["updated_at"])
            user = User(**cached_user)
        else:
            # Cache miss - get from database
            user = await user_crud.get_user_by_cognito_id(db, cognito_user_id)
            
            # Cache user if found
            if user:
                user_cache_data = {
                    'id': user.id,
                    'email': user.email,
                    'name': user.name,
                    'is_active': user.is_active,
                    'cognito_user_id': user.cognito_user_id,
                    'created_at': user.created_at.isoformat() if user.created_at else None,
                    'updated_at': user.updated_at.isoformat() if user.updated_at else None,
                }
                await cache_service.set("user", cognito_user_id, user_cache_data, ttl_seconds=600)
        
        if not user:
            # Create new user with email and cognito_user_id
            user_data = {
                'cognito_user_id': cognito_user_id,
                'email': token_email,  # May be None for some auth methods (social, magic link, phone)
                'name': token_email,   # Use email as name if available, otherwise None
                'is_active': True
            }
            user = await user_crud.create_user_from_cognito(db, user_data)
            auth_logger.info("Created new user from Cognito token",
                           user_email=user_data['email'] or 'not_provided',
                           cognito_user_id=cognito_user_id,
                           event_type="user_creation")
            
            # Cache newly created user
            user_cache_data = {
                'id': user.id,
                'email': user.email,
                'name': user.name,
                'is_active': user.is_active,
                'cognito_user_id': user.cognito_user_id,
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'updated_at': user.updated_at.isoformat() if user.updated_at else None,
            }
            await cache_service.set("user", cognito_user_id, user_cache_data, ttl_seconds=600)
            
            # If email is missing, try to refresh from Cognito
            if not token_email:
                auth_logger.info("Email missing from JWT, refreshing from Cognito",
                               user_id=user.id,
                               cognito_user_id=cognito_user_id,
                               event_type="auto_refresh_new_user")
                try:
                    updated_user = await user_crud.update_user_from_cognito(db, db_user=user, cognito_service=cognito_service)
                    if updated_user and updated_user.email:
                        user = updated_user
                        auth_logger.info("Successfully refreshed new user from Cognito",
                                       user_id=user.id,
                                       user_email=user.email,
                                       event_type="auto_refresh_success")
                    else:
                        auth_logger.info("User created without email (auth method doesn't provide it)",
                                       user_id=user.id,
                                       event_type="user_no_email")
                except Exception as e:
                    auth_logger.warning("Failed to refresh new user from Cognito",
                                      user_id=user.id,
                                      error=str(e),
                                      event_type="auto_refresh_failed")
            
        else:
            # Always check if user data needs updating
            needs_update = False
            update_data = {}
            
            # Check if JWT has email and it's different from stored email
            if token_email and token_email != user.email:
                update_data['email'] = token_email
                update_data['name'] = token_email  # Also update name to match email
                needs_update = True
                auth_logger.info("Updating user email from JWT token",
                               old_email=user.email or 'not_set',
                               new_email=token_email,
                               user_id=user.id,
                               event_type="user_email_update_from_jwt")
            
            # If email is missing and JWT doesn't have email, try refreshing from Cognito
            elif not user.email and not token_email:
                auth_logger.info("Email needs refresh from Cognito",
                               user_id=user.id,
                               cognito_user_id=cognito_user_id,
                               current_email=user.email or 'not_set',
                               has_jwt_email=bool(token_email),
                               event_type="auto_refresh_existing_user")
                try:
                    refreshed_user = await user_crud.update_user_from_cognito(db, db_user=user, cognito_service=cognito_service)
                    if refreshed_user and refreshed_user.email != user.email:
                        user = refreshed_user
                        auth_logger.info("Successfully refreshed existing user from Cognito",
                                       user_id=user.id,
                                       old_email=user.email or 'not_set',
                                       new_email=refreshed_user.email or 'still_not_available',
                                       event_type="auto_refresh_success")
                    else:
                        auth_logger.debug("Cognito refresh returned same/no email",
                                        user_id=user.id,
                                        email=user.email or 'not_available',
                                        event_type="auto_refresh_no_change")
                except Exception as e:
                    auth_logger.warning("Failed to refresh existing user from Cognito",
                                      user_id=user.id,
                                      error=str(e),
                                      event_type="auto_refresh_failed")
            
            # If we have JWT-based updates, apply them
            if needs_update:
                user = await user_crud.update_user(db, db_user=user, user_in=update_data)
                auth_logger.info("Updated user with email from JWT token",
                               user_id=user.id,
                               event_type="user_update_complete")
                
                # Invalidate user cache on update
                await cache_service.delete("user", cognito_user_id)
        
        return user
        
    except httpx.HTTPError as e:
        auth_logger.error("Could not fetch Cognito JWKS",
                         error=str(e),
                         event_type="jwks_fetch_error")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not fetch Cognito JWKS"
        )
    except JWTError as e:
        auth_logger.error("JWT validation error",
                         error=str(e),
                         event_type="jwt_validation_error")
        raise credentials_exception
    except Exception as e:
        auth_logger.error("Unexpected error in get_current_user",
                         error=str(e),
                         event_type="auth_error",
                         exc_info=True)
        
        # Provide more specific error details for debugging
        error_detail = f"Authentication error: {str(e)}"
        if "database" in str(e).lower() or "connection" in str(e).lower():
            error_detail = f"Database connection error during authentication: {str(e)}"
        elif "cognito" in str(e).lower() or "jwks" in str(e).lower():
            error_detail = f"Cognito/JWKS error during authentication: {str(e)}"
        elif "user" in str(e).lower():
            error_detail = f"User lookup/creation error: {str(e)}"
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail
        )

# ---------------------------------------------------------------------------
# Combined API key authentication result
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class APIKeyAuth:
    """Immutable result of API key authentication containing both User and APIKey."""
    user: User
    api_key: Optional[APIKey]


# ---------------------------------------------------------------------------
# Core: single-pass API key validation
# ---------------------------------------------------------------------------

async def get_api_key_auth(
    api_key_str: str = Security(api_key_header),
) -> APIKeyAuth:
    """
    Single-pass API key authentication returning both User and APIKey.

    Validates the API key, resolves both the associated User and APIKey objects
    in one cache lookup / one database query.  Uses Redis read-through caching
    to minimise database load.

    FastAPI's dependency-injection cache ensures this runs at most once per
    request, even when multiple route parameters depend on it via the thin
    wrappers ``get_api_key_user`` and ``get_current_api_key``.

    Args:
        api_key_str: Raw Authorization header value ("Bearer sk-…" or "sk-…")

    Returns:
        APIKeyAuth containing authenticated User and validated APIKey

    Raises:
        HTTPException 401: Invalid / missing API key or no associated user
        HTTPException 500: Unexpected errors
    """
    # ── Local testing bypass ────────────────────────────────────────────
    from src.core.local_testing import is_local_testing_mode, get_or_create_test_user
    if is_local_testing_mode():
        auth_logger.info("Using local testing mode - bypassing API key validation",
                        event_type="local_testing_bypass")
        async with get_db() as db:
            test_user = await get_or_create_test_user(db)
            user_dict = {
                'id': test_user.id,
                'email': test_user.email,
                'name': test_user.name,
                'is_active': test_user.is_active,
                'cognito_user_id': test_user.cognito_user_id,
                'created_at': test_user.created_at,
                'updated_at': test_user.updated_at,
            }
            # Fetch the test user's first active API key (if any)
            result = await db.execute(
                select(APIKey)
                .where(APIKey.user_id == test_user.id, APIKey.is_active == True)
                .limit(1)
            )
            test_api_key = result.scalar_one_or_none()
        return APIKeyAuth(user=User(**user_dict), api_key=test_api_key)

    # ── Parse & validate ────────────────────────────────────────────────
    if not api_key_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if api_key_str.startswith("Bearer "):
        api_key_str = api_key_str[7:]

    if not api_key_str.startswith("sk-"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format. Must start with sk-",
            headers={"WWW-Authenticate": "Bearer"},
        )

    key_prefix = api_key_str[:9] if len(api_key_str) >= 9 else api_key_str

    try:
        # ── Single cache lookup ─────────────────────────────────────────
        cached_data = await cache_service.get("api_key", key_prefix)
        if cached_data:
            return await _build_auth_from_cache(cached_data, api_key_str, key_prefix)

        # ── Single DB fallback ──────────────────────────────────────────
        return await _build_auth_from_db(api_key_str, key_prefix)

    except HTTPException:
        raise
    except Exception as e:
        auth_logger.error("Error in get_api_key_auth",
                         error=str(e),
                         key_prefix=key_prefix,
                         event_type="api_key_auth_error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error validating API key: {str(e)}"
        )


# ---------------------------------------------------------------------------
# Cache / DB resolution helpers (private)
# ---------------------------------------------------------------------------

async def _build_auth_from_cache(
    cached_data: dict,
    raw_key: str,
    key_prefix: str,
) -> APIKeyAuth:
    """Construct APIKeyAuth from a cache hit, with hash verification."""

    # ── Key verification ────────────────────────────────────────────────
    if cached_data.get("encrypted_key") is not None:
        # Modern key – full hash verification
        if not verify_api_key(raw_key, cached_data.get("hashed_key")):
            auth_logger.error("Cached API key hash validation failed",
                             key_prefix=key_prefix,
                             event_type="cached_api_key_validation_failed")
            await cache_service.delete("api_key", key_prefix)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        auth_logger.debug("Legacy API key verified (cached, prefix-only)",
                        key_prefix=key_prefix,
                        event_type="cached_legacy_api_key_verified")

    # ── Background last-used update (non-blocking) ──────────────────────
    asyncio.create_task(_update_api_key_last_used_background(cached_data.get("id")))

    # ── Deserialize User ────────────────────────────────────────────────
    ud = cached_data["user"]
    user = User(
        id=ud["id"],
        email=ud.get("email"),
        name=ud.get("name"),
        is_active=ud.get("is_active", True),
        cognito_user_id=ud.get("cognito_user_id"),
        created_at=datetime.fromisoformat(ud["created_at"]) if ud.get("created_at") else None,
        updated_at=datetime.fromisoformat(ud["updated_at"]) if ud.get("updated_at") else None,
    )

    # ── Deserialize APIKey ──────────────────────────────────────────────
    api_key = APIKey(
        id=cached_data["id"],
        user_id=cached_data["user_id"],
        key_prefix=cached_data["key_prefix"],
        hashed_key=cached_data.get("hashed_key"),
        encrypted_key=cached_data.get("encrypted_key"),
        is_active=cached_data.get("is_active", True),
        last_used_at=(datetime.fromisoformat(cached_data["last_used_at"])
                      if cached_data.get("last_used_at") else None),
        created_at=(datetime.fromisoformat(cached_data["created_at"])
                    if isinstance(cached_data.get("created_at"), str)
                    else cached_data.get("created_at")),
        name=cached_data.get("name"),
        encryption_version=cached_data.get("encryption_version"),
        is_default=cached_data.get("is_default"),
    )

    return APIKeyAuth(user=user, api_key=api_key)


async def _build_auth_from_db(
    raw_key: str,
    key_prefix: str,
) -> APIKeyAuth:
    """Fetch APIKey + User from database, verify hash, cache, and return."""

    async with get_db() as db:
        result = await db.execute(
            select(APIKey)
            .options(joinedload(APIKey.user))
            .where(APIKey.key_prefix == key_prefix)
        )
        db_api_key = result.scalar_one_or_none()

        if not db_api_key:
            auth_logger.error("Could not find API key",
                             key_prefix=key_prefix,
                             event_type="api_key_not_found")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # ── Verify hash ────────────────────────────────────────────────
        if db_api_key.encrypted_key is not None:
            if not verify_api_key(raw_key, db_api_key.hashed_key):
                auth_logger.error("API key hash validation failed",
                                 key_prefix=key_prefix,
                                 event_type="api_key_validation_failed")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        else:
            auth_logger.info("Legacy API key verified (prefix-only)",
                           key_prefix=key_prefix,
                           event_type="legacy_api_key_verified")

        # ── Update last-used ────────────────────────────────────────────
        await api_key_crud.update_last_used(db, db_api_key)

        # ── Validate user ───────────────────────────────────────────────
        db_user = db_api_key.user
        if not db_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key not associated with a valid user",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # ── Build return objects ────────────────────────────────────────
        user_dict = {
            'id': db_user.id,
            'email': db_user.email,
            'name': db_user.name,
            'is_active': db_user.is_active,
            'cognito_user_id': db_user.cognito_user_id,
            'created_at': db_user.created_at,
            'updated_at': db_user.updated_at,
        }
        api_key_dict = {
            'id': db_api_key.id,
            'user_id': db_api_key.user_id,
            'key_prefix': db_api_key.key_prefix,
            'hashed_key': db_api_key.hashed_key,
            'encrypted_key': db_api_key.encrypted_key,
            'is_active': db_api_key.is_active,
            'last_used_at': db_api_key.last_used_at,
            'created_at': db_api_key.created_at,
            'name': db_api_key.name,
            'encryption_version': db_api_key.encryption_version,
            'is_default': db_api_key.is_default,
        }

        # ── Write-through cache ─────────────────────────────────────────
        cache_payload = {
            **api_key_dict,
            'last_used_at': db_api_key.last_used_at.isoformat() if db_api_key.last_used_at else None,
            'created_at': db_api_key.created_at.isoformat() if db_api_key.created_at else None,
            'user': {
                **user_dict,
                'created_at': db_user.created_at.isoformat() if db_user.created_at else None,
                'updated_at': db_user.updated_at.isoformat() if db_user.updated_at else None,
            },
        }
        await cache_service.set("api_key", key_prefix, cache_payload, ttl_seconds=300)

    return APIKeyAuth(
        user=User(**user_dict),
        api_key=APIKey(**api_key_dict),
    )


# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------

async def _update_api_key_last_used_background(api_key_id: int) -> None:
    """Background task to update API key last_used_at timestamp."""
    try:
        async with get_db() as db:
            result = await db.execute(select(APIKey).where(APIKey.id == api_key_id))
            api_key = result.scalar_one_or_none()
            if api_key:
                await api_key_crud.update_last_used(db, api_key)
    except Exception as e:
        auth_logger.warning(
            "Background API key last_used update failed",
            api_key_id=api_key_id,
            error=str(e),
            event_type="background_last_used_update_failed",
        )


# ---------------------------------------------------------------------------
# Thin wrappers – preserve existing dependency interface
# ---------------------------------------------------------------------------

async def get_api_key_user(
    auth: APIKeyAuth = Depends(get_api_key_auth),
) -> User:
    """
    Return the authenticated User for an API-key-secured request.

    Thin wrapper around ``get_api_key_auth``; FastAPI's DI cache ensures
    the underlying auth work is performed at most once per request.
    """
    return auth.user


async def get_current_api_key(
    auth: APIKeyAuth = Depends(get_api_key_auth),
) -> APIKey:
    """
    Return the validated APIKey object for an API-key-secured request.

    Thin wrapper around ``get_api_key_auth``; FastAPI's DI cache ensures
    the underlying auth work is performed at most once per request.
    """
    if auth.api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return auth.api_key


# ---------------------------------------------------------------------------
# Type aliases for commonly used dependency chains
# ---------------------------------------------------------------------------
CurrentUser = Annotated[User, Depends(get_current_user)]
APIKeyUser = Annotated[User, Depends(get_api_key_user)]
CurrentAPIKey = Annotated[APIKey, Depends(get_current_api_key)]
APIKeyAuthentication = Annotated[APIKeyAuth, Depends(get_api_key_auth)]
DBSession = Annotated[AsyncSession, Depends(get_db_session)]