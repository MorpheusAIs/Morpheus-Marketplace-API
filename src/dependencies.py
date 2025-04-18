from typing import Annotated, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status, Security
from fastapi.security import HTTPBearer, APIKeyHeader
from jose import jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import select
from sqlalchemy.future import select as future_select
import time
import logging
from datetime import datetime, timedelta

from src.core.config import settings
from src.core.security import verify_api_key
from src.crud import user as user_crud
from src.crud import api_key as api_key_crud
from src.db.database import get_db
from src.db.models import User, APIKey
from src.schemas.token import TokenPayload

# Define bearer token scheme for JWT authentication
oauth2_scheme = HTTPBearer(
    auto_error=True,
    description="JWT Bearer token authentication"
)

# Define API key scheme for API key authentication
api_key_header = APIKeyHeader(
    name="Authorization", 
    auto_error=False,
    description="Provide the API key as 'Bearer sk-xxxxxx'"
)

async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(oauth2_scheme)
) -> User:
    """
    Get the current authenticated user from JWT token.
    
    Args:
        db: Database session
        token_data: JWT token from Authorization header
        
    Returns:
        User object
        
    Raises:
        HTTPException: If authentication fails
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # Get the token from the scheme
        token = token_data.credentials
        
        # Decode the JWT token
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        
        # Extract user ID and token type
        user_id: Optional[str] = payload.get("sub")
        token_type: Optional[str] = payload.get("type")
        
        # Check token and user ID validity
        if user_id is None or token_type != "access":
            raise credentials_exception
        
        token_data = TokenPayload(sub=user_id, type=token_type)
    except JWTError:
        raise credentials_exception
    
    # Get the user from the database
    user = await user_crud.get_user_by_id(db, int(token_data.sub))
    
    # Check if user exists and is active
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        
    return user

async def get_api_key_user(
    db: AsyncSession = Depends(get_db),
    api_key: str = Security(api_key_header)
) -> Optional[User]:
    """
    Validate an API key and return the associated user.
    
    The API key is expected in the format: "Bearer sk-xxxxxx" or just "sk-xxxxxx"
    
    Args:
        db: Database session
        api_key: API key from Authorization header
        
    Returns:
        User object if API key is valid
        
    Raises:
        HTTPException: If API key is invalid or missing
    """
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

# Type aliases for commonly used dependency chains
CurrentUser = Annotated[User, Depends(get_current_user)]
APIKeyUser = Annotated[User, Depends(get_api_key_user)]
DBSession = Annotated[AsyncSession, Depends(get_db)] 