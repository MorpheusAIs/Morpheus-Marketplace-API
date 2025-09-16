from typing import Annotated, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status, Security
from fastapi.security import HTTPBearer, APIKeyHeader, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import select
from sqlalchemy.future import select as future_select
import time
import logging
from datetime import datetime, timedelta
import boto3
from jose import jwt, jwk
from jose.utils import base64url_decode
import requests

from src.core.config import settings
from src.core.security import verify_api_key
from src.crud import user as user_crud
from src.crud import api_key as api_key_crud
from src.db.database import get_db
from src.db.models import User, APIKey
from src.schemas.token import TokenPayload
from src.services.cognito_service import cognito_service

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
    db: AsyncSession = Depends(get_db),
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
        logging.info("🧪 Using local testing mode - bypassing Cognito authentication")
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
        logging.info(f"🔍 JWT Validation Debug:")
        logging.info(f"   Token (first 20 chars): {token.credentials[:20]}...")
        logging.info(f"   Expected audience: {settings.COGNITO_CLIENT_ID}")
        logging.info(f"   Expected issuer: https://cognito-idp.{settings.COGNITO_REGION}.amazonaws.com/{settings.COGNITO_USER_POOL_ID}")
        
        # Fetch JWKS from Cognito
        jwks_url = settings.COGNITO_JWKS_URL
        logging.info(f"   JWKS URL: {jwks_url}")
        jwks_response = requests.get(jwks_url)
        jwks_response.raise_for_status()
        jwks = jwks_response.json()
        
        # Get the key ID from token header
        header = jwt.get_unverified_header(token.credentials)
        logging.info(f"   Token header: {header}")
        kid = header.get('kid')
        if not kid:
            logging.error("❌ No 'kid' found in token header")
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
            logging.error(f"❌ Client ID mismatch: expected {settings.COGNITO_CLIENT_ID}, got {token_client_id}")
            raise credentials_exception
        
        logging.info(f"✅ JWT decode successful!")
        logging.info(f"   Payload audience (aud): {payload.get('aud')}")
        logging.info(f"   Payload issuer (iss): {payload.get('iss')}")
        logging.info(f"   Payload subject (sub): {payload.get('sub')}")
        logging.info(f"   Payload email: {payload.get('email')}")
        
        # Extract user information from token
        cognito_user_id = payload.get('sub')
        token_email = payload.get('email')
        
        if not cognito_user_id:
            logging.error(f"❌ Missing cognito_user_id (sub): {cognito_user_id}")
            raise credentials_exception
            
        # Get or create local user record
        user = await user_crud.get_user_by_cognito_id(db, cognito_user_id)
        
        if not user:
            # Create new user with email and cognito_user_id
            user_data = {
                'cognito_user_id': cognito_user_id,
                'email': token_email or cognito_user_id,  # Use real email if available, fallback to cognito_user_id
                'name': token_email or cognito_user_id,   # Use email as name, fallback to cognito_user_id
                'is_active': True
            }
            user = await user_crud.create_user_from_cognito(db, user_data)
            logging.info(f"✅ Created new user with email: {user_data['email']}")
            
        else:
            # Check if existing user has placeholder data and update with real email
            needs_update = False
            update_data = {}
            
            # Check if email is still a placeholder (equals cognito_user_id)
            if user.email == cognito_user_id and token_email and token_email != cognito_user_id:
                update_data['email'] = token_email
                update_data['name'] = token_email  # Also update name to match email
                needs_update = True
                logging.info(f"📧 Updating placeholder email with real email: {token_email}")
            
            # If we have updates, apply them
            if needs_update:
                user = await user_crud.update_user(db, db_user=user, user_in=update_data)
                logging.info(f"✅ Updated user with real email from JWT token")
        
        return user
        
    except requests.RequestException as e:
        logging.error(f"❌ Could not fetch Cognito JWKS: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not fetch Cognito JWKS"
        )
    except JWTError as e:
        logging.error(f"❌ JWT validation error: {str(e)}")
        logging.error(f"   This usually means: token expired, wrong audience, wrong issuer, or invalid signature")
        raise credentials_exception
    except Exception as e:
        logging.error(f"❌ Unexpected error in get_current_user: {str(e)}")
        import traceback
        logging.error(f"   Traceback: {traceback.format_exc()}")
        
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

async def get_api_key_user(
    db: AsyncSession = Depends(get_db),
    api_key: str = Security(api_key_header)
) -> Optional[User]:
    """
    Validate an API key and return the associated user.
    
    The API key is expected in the format: "Bearer sk-xxxxxx" or just "sk-xxxxxx"
    
    In local testing mode, bypasses API key validation and returns test user.
    
    Args:
        db: Database session
        api_key: API key from Authorization header
        
    Returns:
        User object if API key is valid
        
    Raises:
        HTTPException: If API key is invalid or missing
    """
    # Local testing bypass
    from src.core.local_testing import is_local_testing_mode, get_or_create_test_user
    if is_local_testing_mode():
        logging.info("🧪 Using local testing mode - bypassing API key validation")
        return await get_or_create_test_user(db)
    
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    # Extract the key without the Bearer prefix if present
    if api_key.startswith("Bearer "):
        api_key = api_key.replace("Bearer ", "")
    
    # Validate API key format
    if not api_key.startswith("sk-"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format. Must start with sk-",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Extract the key prefix - "sk-" plus the next 6 characters 
    # This should match how keys are generated in security.py
    key_prefix = api_key[:9] if len(api_key) >= 9 else api_key
    
    try:
        # Instead of just getting the API key, join with the user table to avoid lazy loading
        # And also load the user's api_keys relationship to avoid another lazy load
        
        # First get the API key and its user with a subquery
        api_key_query = select(APIKey).where(APIKey.key_prefix == key_prefix)
        db_api_key = (await db.execute(api_key_query)).scalar_one_or_none()
        
        if not db_api_key:
            # Log the key prefix for debugging
            logging.error(f"Could not find API key with prefix: {key_prefix}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Validate the full API key against the stored hash
        if not verify_api_key(api_key, db_api_key.hashed_key):
            logging.error(f"API key hash validation failed for prefix: {key_prefix}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Update last used timestamp
        await api_key_crud.update_last_used(db, db_api_key)
        
        # Now get the user with api_keys loaded
        user_query = future_select(User).options(
            selectinload(User.api_keys)
        ).where(User.id == db_api_key.user_id)
        
        user = (await db.execute(user_query)).scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key not associated with a valid user",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        return user
        
    except Exception as e:
        # Log the error
        logging.error(f"Error in get_api_key_user: {str(e)}")
        
        # Re-raise HTTP exceptions
        if isinstance(e, HTTPException):
            raise
            
        # Otherwise raise a generic error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error validating API key: {str(e)}"
        )

async def get_current_api_key(
    db: AsyncSession = Depends(get_db),
    api_key_str: str = Security(api_key_header)
) -> APIKey:
    """
    Validate an API key and return the APIKey object.
    
    The API key is expected in the format: "Bearer sk-xxxxxx" or just "sk-xxxxxx"
    
    Args:
        db: Database session
        api_key_str: API key from Authorization header
        
    Returns:
        APIKey object if valid
        
    Raises:
        HTTPException: If API key is invalid or missing
    """
    if not api_key_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    # Extract the key without the Bearer prefix if present
    if api_key_str.startswith("Bearer "):
        api_key_str = api_key_str.replace("Bearer ", "")
    
    # Validate API key format
    if not api_key_str.startswith("sk-"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format. Must start with sk-",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Extract the key prefix - "sk-" plus the next 6 characters 
    key_prefix = api_key_str[:9] if len(api_key_str) >= 9 else api_key_str
    
    try:
        # Get the API key with its user relationship loaded
        api_key_query = select(APIKey).options(
            joinedload(APIKey.user)
        ).where(APIKey.key_prefix == key_prefix, APIKey.is_active == True)
        
        db_api_key = (await db.execute(api_key_query)).scalar_one_or_none()
        
        if not db_api_key:
            # Log the key prefix for debugging
            logging.error(f"Could not find API key with prefix: {key_prefix}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Validate the full API key against the stored hash
        if not verify_api_key(api_key_str, db_api_key.hashed_key):
            logging.error(f"API key hash validation failed for prefix: {key_prefix}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Update last used timestamp
        await api_key_crud.update_last_used(db, db_api_key)
        
        return db_api_key
        
    except Exception as e:
        # Log the error
        logging.error(f"Error in get_current_api_key: {str(e)}")
        
        # Re-raise HTTP exceptions
        if isinstance(e, HTTPException):
            raise
            
        # Otherwise raise a generic error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error validating API key: {str(e)}"
        )

# Type aliases for commonly used dependency chains
CurrentUser = Annotated[User, Depends(get_current_user)]
APIKeyUser = Annotated[User, Depends(get_api_key_user)]
CurrentAPIKey = Annotated[APIKey, Depends(get_current_api_key)]
DBSession = Annotated[AsyncSession, Depends(get_db)] 