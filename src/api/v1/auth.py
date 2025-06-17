# Authentication routes 
from typing import List, Any, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status, Depends, Body, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import create_access_token, create_refresh_token
from src.crud import user as user_crud
from src.crud import api_key as api_key_crud
from src.crud import private_key as private_key_crud
from src.crud import delegation as delegation_crud
from src.db.database import get_db
from src.schemas.user import UserCreate, UserResponse, UserLogin, UserDeletionResponse
from src.schemas.token import Token, TokenRefresh, TokenPayload
from src.schemas.api_key import APIKeyCreate, APIKeyResponse, APIKeyDB
from src.schemas import private_key as private_key_schemas
from src.schemas import delegation as delegation_schemas
from src.dependencies import CurrentUser
from src.db.models import User
from src.core.config import settings

router = APIRouter(tags=["Auth"])

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    user_in: UserCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    Register a new user.
    """
    # Check if user already exists
    existing_user = await user_crud.get_user_by_email(db, user_in.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists"
        )
    
    # Create user
    user = await user_crud.create_user(db, user_in)
    
    return user

@router.post("/login", response_model=Token)
async def login(
    user_in: UserLogin,
    db: AsyncSession = Depends(get_db)
):
    """
    Log in a user and return JWT tokens.
    
    Simply provide your email and password directly in the request body:
    
    ```json
    {
        "email": "user@example.com",
        "password": "yourpassword"
    }
    ```
    
    The response will contain an access_token that should be used in the Authorization header
    for protected endpoints, with the format: `Bearer {access_token}`
    """
    # Authenticate user
    user = await user_crud.authenticate_user(db, user_in.email, user_in.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create tokens
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@router.post("/refresh", response_model=Token)
async def refresh_token(
    refresh_token_in: TokenRefresh,
    db: AsyncSession = Depends(get_db)
):
    """
    Get a new access token using a refresh token.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # Decode the refresh token
        from jose import jwt, JWTError
        
        payload = jwt.decode(
            refresh_token_in.refresh_token, 
            settings.JWT_SECRET_KEY, 
            algorithms=[settings.JWT_ALGORITHM]
        )
        
        # Extract user ID and token type
        user_id = payload.get("sub")
        token_type = payload.get("type")
        
        # Check token type and user ID
        if token_type != "refresh" or not user_id:
            raise credentials_exception
            
        # Get user from database
        user = await user_crud.get_user_by_id(db, int(user_id))
        if not user or not user.is_active:
            raise credentials_exception
            
        # Create new tokens
        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }
        
    except JWTError:
        raise credentials_exception

@router.post("/keys", response_model=APIKeyResponse)
async def create_api_key(
    api_key_in: APIKeyCreate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new API key for the current user.
    
    Requires JWT Bearer authentication with the token received from the login endpoint.
    """
    # Create API key
    api_key, full_key = await api_key_crud.create_api_key(db, current_user.id, api_key_in)
    
    return {
        "key": full_key,
        "key_prefix": api_key.key_prefix,
        "name": api_key.name
    }

@router.get("/keys", response_model=List[APIKeyDB])
async def get_api_keys(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db)
):
    """
    Get all API keys for the current user.
    
    Requires JWT Bearer authentication with the token received from the login endpoint.
    """
    api_keys = await api_key_crud.get_user_api_keys(db, current_user.id)
    return api_keys

@router.delete("/keys/{key_id}", response_model=APIKeyDB)
async def delete_api_key(
    key_id: int,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db)
):
    """
    Deactivate an API key.
    
    Requires JWT Bearer authentication with the token received from the login endpoint.
    """
    # Deactivate API key
    api_key = await api_key_crud.deactivate_api_key(db, key_id, current_user.id)
    
    # Check if API key exists and belongs to the user
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found"
        )
    
    return api_key

# Private key management endpoints
@router.post("/private-key", status_code=status.HTTP_201_CREATED, response_model=dict)
async def store_private_key(
    request_body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(CurrentUser)
):
    """
    Store an encrypted blockchain private key for the authenticated user.
    Replaces any existing key.
    """
    # Validate request body manually
    if "private_key" not in request_body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Private key is required"
        )
    
    private_key = request_body["private_key"]
    
    try:
        await private_key_crud.create_user_private_key(
            db=db, 
            user_id=current_user.id, 
            private_key=private_key
        )
        return {"message": "Private key stored successfully"}
    except Exception as e:
        # In a production environment, we should have proper error logging
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store private key"
        )

@router.get("/private-key", response_model=private_key_schemas.PrivateKeyStatus)
async def get_private_key_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(CurrentUser)
):
    """
    Check if a user has a private key registered.
    Does not return the actual key, only status information.
    """
    has_key = await private_key_crud.user_has_private_key(db, current_user.id)
    return {"has_private_key": has_key}

@router.delete("/private-key", status_code=status.HTTP_200_OK, response_model=dict)
async def delete_private_key(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(CurrentUser)
):
    """
    Delete a user's private key.
    """
    has_key = await private_key_crud.user_has_private_key(db, current_user.id)
    if not has_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Private key not found"
        )
    
    success = await private_key_crud.delete_user_private_key(db, current_user.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete private key"
        )
    
    return {"message": "Private key deleted successfully"}

# --- Delegation Endpoints --- 
@router.post("/delegation", response_model=delegation_schemas.DelegationRead)
async def store_delegation(
    delegation_in: delegation_schemas.DelegationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(CurrentUser)
):
    """
    Allows an authenticated user to store a signed delegation.
    The frontend should construct and sign the delegation using the Gator SDK.
    """
    if delegation_in.delegate_address != settings.GATEWAY_DELEGATE_ADDRESS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Delegation must be granted to the configured gateway address: {settings.GATEWAY_DELEGATE_ADDRESS}"
        )

    existing_active = await delegation_crud.get_active_delegation_by_user(db, user_id=current_user.id)
    if existing_active:
        await delegation_crud.set_delegation_inactive(db, db_delegation=existing_active)

    db_delegation = await delegation_crud.create_user_delegation(
        db=db, delegation=delegation_in, user_id=current_user.id
    )
    return db_delegation

@router.get("/delegation", response_model=List[delegation_schemas.DelegationRead])
async def get_user_delegations(
    skip: int = 0,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(CurrentUser)
):
    """
    Retrieves the user's stored delegations.
    """
    delegations = await delegation_crud.get_delegations_by_user(db, user_id=current_user.id, skip=skip, limit=limit)
    return delegations

@router.get("/delegation/active", response_model=Optional[delegation_schemas.DelegationRead])
async def get_active_user_delegation(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(CurrentUser)
):
    """
    Retrieves the user's currently active delegation, if any.
    """
    delegation = await delegation_crud.get_active_delegation_by_user(db, user_id=current_user.id)
    return delegation

@router.delete("/delegation/{delegation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_delegation(
    delegation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(CurrentUser)
):
    """
    Deletes a specific delegation for the user.
    Alternatively, could just mark it inactive.
    """
    db_delegation = await delegation_crud.get_delegation(db, delegation_id=delegation_id)
    if not db_delegation or db_delegation.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delegation not found")

    # Using hard delete for now
    await delegation_crud.delete_delegation(db, db_delegation=db_delegation)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
# --- End Delegation Endpoints ---

@router.delete("/register", response_model=UserDeletionResponse, status_code=status.HTTP_200_OK)
async def delete_user_account(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db)
):
    """
    Delete the current user's account and all associated data.
    
    This action is irreversible and will:
    1. Delete all API keys
    2. Delete private key data (via cascade)
    3. Delete automation settings (via cascade)
    4. Delete delegation data (via cascade)
    5. Delete the user account
    
    Sessions are left to expire naturally.
    
    Requires JWT Bearer authentication.
    """
    user_id = current_user.id
    
    try:
        # 1. Delete all API keys manually (no cascade relationship)
        api_keys_deleted = await api_key_crud.delete_all_user_api_keys(db, user_id)
        
        # 2. Delete the user (this will cascade delete private keys, automation settings, and delegations)
        deleted_user = await user_crud.delete_user(db, user_id)
        
        if not deleted_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Prepare response data
        deleted_data = {
            "api_keys": api_keys_deleted,
            "private_key": True,  # Will be deleted via cascade if it exists
            "automation_settings": True,  # Will be deleted via cascade if it exists
            "delegations": True  # Will be deleted via cascade if any exist
        }
        
        # Use timezone-aware datetime and convert to naive for consistency
        deleted_at_with_tz = datetime.now(timezone.utc)
        deleted_at = deleted_at_with_tz.replace(tzinfo=None)
        
        return UserDeletionResponse(
            message="User account successfully deleted",
            deleted_data=deleted_data,
            user_id=user_id,
            deleted_at=deleted_at
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Log the error (in production, use proper logging)
        print(f"Error deleting user account {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete user account"
        )

# Export router
auth_router = router 