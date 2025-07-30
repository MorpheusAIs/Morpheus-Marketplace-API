# Authentication routes 
from typing import List, Any, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status, Depends, Body, Request, Response, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.crud import user as user_crud
from src.crud import api_key as api_key_crud
from src.crud import private_key as private_key_crud
from src.crud import delegation as delegation_crud
from src.crud import session as session_crud
from src.db.database import get_db
from src.schemas.user import UserDeletionResponse
from src.schemas.api_key import APIKeyCreate, APIKeyResponse, APIKeyDB
from src.schemas import private_key as private_key_schemas
from src.schemas import delegation as delegation_schemas
from src.dependencies import CurrentUser
from src.db.models import User
from src.core.config import settings
from src.services.cognito_service import cognito_service

router = APIRouter(tags=["Auth"])

# Note: Authentication is now handled by Cognito
# Users authenticate via Cognito OAuth2 flow and receive JWT tokens
# The frontend should redirect to Cognito for login/registration

# OAuth2 callback is handled by the /docs/oauth2-redirect endpoint

@router.get("/me", response_model=dict)
async def get_current_user_info(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    refresh_from_cognito: bool = Query(False, description="Fetch fresh user data from Cognito")
):
    """
    Get current user information.
    
    Requires JWT Bearer authentication with Cognito token.
    
    Args:
        refresh_from_cognito: If True, fetches fresh user data from Cognito and updates the database
    """
    user = current_user
    
    # If refresh_from_cognito is requested, try to get fresh data from Cognito
    if refresh_from_cognito:
        try:
            updated_user = await user_crud.update_user_from_cognito(
                db, db_user=user, cognito_service=cognito_service
            )
            if updated_user:
                user = updated_user
                data_source = "cognito_refreshed"
            else:
                data_source = "database_cached"
        except Exception as e:
            data_source = "database_cached"
    else:
        data_source = "database_cached"
    
    # Return user data focusing on email and cognito_id
    response_data = {
        "id": user.id,
        "cognito_user_id": user.cognito_user_id,
        "email": user.email,
        "name": user.name,
        "is_active": user.is_active,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "data_source": data_source
    }
    
    # Simple indicator if email is still a placeholder
    if user.email == user.cognito_user_id:
        response_data["note"] = "Email not yet available from Cognito - try refresh_from_cognito=true"
    
    return response_data

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
    1. Delete all sessions
    2. Delete all API keys
    3. Delete private key data (via cascade)
    4. Delete automation settings (via cascade)
    5. Delete delegation data (via cascade)
    6. Delete the user account
    7. Delete/deactivate the Cognito identity
    
    Requires JWT Bearer authentication.
    """
    user_id = current_user.id
    cognito_user_id = current_user.cognito_user_id
    
    try:
        # 1. Delete all sessions first (to avoid foreign key constraint violations)
        sessions_deleted = await session_crud.delete_all_user_sessions(db, user_id)
        
        # 2. Delete all API keys manually (no cascade relationship)
        api_keys_deleted = await api_key_crud.delete_all_user_api_keys(db, user_id)
        
        # 3. Delete the user (this will cascade delete private keys, automation settings, and delegations)
        deleted_user = await user_crud.delete_user(db, user_id)
        
        if not deleted_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # 4. Delete the user from Cognito User Pool
        cognito_deletion_result = await cognito_service.delete_user(cognito_user_id)
        
        # Prepare response data
        deleted_data = {
            "sessions": sessions_deleted,
            "api_keys": api_keys_deleted,
            "private_key": True,  # Will be deleted via cascade if it exists
            "automation_settings": True,  # Will be deleted via cascade if it exists
            "delegations": True  # Will be deleted via cascade if any exist
        }
        
        # Use timezone-aware datetime and convert to naive for consistency
        deleted_at_with_tz = datetime.now(timezone.utc)
        deleted_at = deleted_at_with_tz.replace(tzinfo=None)
        
        # Determine overall success message
        if cognito_deletion_result["success"]:
            message = "User account successfully deleted from both database and Cognito"
        else:
            message = "User account deleted from database, but Cognito deletion failed"
        
        return UserDeletionResponse(
            message=message,
            deleted_data=deleted_data,
            cognito_deletion=cognito_deletion_result,
            user_id=user_id,
            deleted_at=deleted_at
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Log the error properly
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error deleting user account {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete user account"
        )

# Export router
auth_router = router 