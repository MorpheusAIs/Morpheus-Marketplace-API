# Authentication routes 
from typing import List, Any, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status, Depends, Body, Request, Response, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....crud import user as user_crud
from ....crud import api_key as api_key_crud
from ....crud import private_key as private_key_crud
from ....crud import delegation as delegation_crud
from ....crud import session as session_crud
from ....db.database import get_db
from ....schemas.user import UserDeletionResponse
from ....schemas.api_key import APIKeyCreate, APIKeyResponse, APIKeyDB
from ....schemas import private_key as private_key_schemas
from ....schemas import delegation as delegation_schemas
from ....dependencies import CurrentUser, get_current_user
from ....db.models import User
from ....core.config import settings
from ....services.cognito_service import cognito_service
from ....core.logging_config import get_auth_logger

logger = get_auth_logger()

router = APIRouter(tags=["Auth"])

# Note: Authentication is now handled by Cognito
# Users authenticate via Cognito OAuth2 flow and receive JWT tokens
# The frontend should redirect to Cognito for login/registration

# OAuth2 callback is handled by the /docs/oauth2-redirect endpoint

@router.get("/me", response_model=dict)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get current user information.
    
    Requires JWT Bearer authentication with Cognito token.
    User data is automatically kept up-to-date during authentication.
    """
    user = current_user
    data_source = "database_auto_updated"
    
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
    
    # Email should now be automatically updated during authentication
    
    return response_data

@router.post("/keys", response_model=APIKeyResponse)
async def create_api_key(
    api_key_in: APIKeyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new API key for the current user.
    
    Requires JWT Bearer authentication with the token received from the login endpoint.
    """
    api_key_logger = logger.bind(endpoint="create_api_key", user_id=current_user.id)
    api_key_logger.info("Creating new API key",
                       key_name=api_key_in.name,
                       event_type="api_key_creation_start")
    
    # Create API key
    api_key, full_key = await api_key_crud.create_api_key(db, current_user.id, api_key_in)
    
    api_key_logger.info("API key created successfully",
                       key_prefix=api_key.key_prefix,
                       key_name=api_key.name,
                       event_type="api_key_created")
    
    return {
        "key": full_key,
        "key_prefix": api_key.key_prefix,
        "name": api_key.name
    }

@router.get("/keys", response_model=List[APIKeyDB])
async def get_api_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all API keys for the current user.
    
    Requires JWT Bearer authentication with the token received from the login endpoint.
    """
    list_logger = logger.bind(endpoint="get_api_keys", user_id=current_user.id)
    list_logger.info("Retrieving user API keys", event_type="api_keys_list_start")
    
    api_keys = await api_key_crud.get_user_api_keys(db, current_user.id)
    
    list_logger.info("Retrieved user API keys successfully",
                    key_count=len(api_keys),
                    event_type="api_keys_listed")
    return api_keys

@router.delete("/keys/{key_id}", response_model=APIKeyDB)
async def delete_api_key(
    key_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Deactivate an API key.
    
    Requires JWT Bearer authentication with the token received from the login endpoint.
    """
    delete_logger = logger.bind(endpoint="delete_api_key", user_id=current_user.id, key_id=key_id)
    delete_logger.info("Attempting to deactivate API key",
                      key_id=key_id,
                      event_type="api_key_deletion_start")
    
    # Deactivate API key
    api_key = await api_key_crud.deactivate_api_key(db, key_id, current_user.id)
    
    # Check if API key exists and belongs to the user
    if not api_key:
        delete_logger.warning("API key not found for deletion",
                             key_id=key_id,
                             event_type="api_key_not_found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found"
        )
    
    delete_logger.info("API key deactivated successfully",
                      key_id=key_id,
                      key_prefix=api_key.key_prefix,
                      event_type="api_key_deleted")
    
    return api_key

# Private key management endpoints
@router.post("/private-key", status_code=status.HTTP_201_CREATED, response_model=dict)
async def store_private_key(
    private_key_data: private_key_schemas.PrivateKeyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Store an encrypted blockchain private key for the authenticated user.
    Replaces any existing key.
    """
    private_key = private_key_data.private_key
    pk_logger = logger.bind(endpoint="store_private_key", user_id=current_user.id)
    
    pk_logger.info("Storing private key for user",
                  event_type="private_key_store_start")
    
    try:
        await private_key_crud.create_user_private_key(
            db=db, 
            user_id=current_user.id, 
            private_key=private_key
        )
        pk_logger.info("Private key stored successfully",
                      event_type="private_key_stored")
        return {"message": "Private key stored successfully"}
    except Exception as e:
        pk_logger.error("Failed to store private key",
                       error=str(e),
                       event_type="private_key_store_failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store private key"
        )

@router.get("/private-key", response_model=private_key_schemas.PrivateKeyStatus)
async def get_private_key_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Check if a user has a private key registered.
    Does not return the actual key, only status information.
    """
    status_logger = logger.bind(endpoint="get_private_key_status", user_id=current_user.id)
    status_logger.debug("Checking private key status", event_type="private_key_status_check")
    
    has_key = await private_key_crud.user_has_private_key(db, current_user.id)
    
    status_logger.info("Private key status checked",
                      has_key=has_key,
                      event_type="private_key_status_retrieved")
    return {"has_key": has_key}

@router.delete("/private-key", status_code=status.HTTP_200_OK, response_model=dict)
async def delete_private_key(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete a user's private key.
    """
    pk_delete_logger = logger.bind(endpoint="delete_private_key", user_id=current_user.id)
    pk_delete_logger.info("Attempting to delete private key", event_type="private_key_deletion_start")
    
    has_key = await private_key_crud.user_has_private_key(db, current_user.id)
    if not has_key:
        pk_delete_logger.warning("Private key not found for deletion",
                                event_type="private_key_not_found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Private key not found"
        )
    
    success = await private_key_crud.delete_user_private_key(db, current_user.id)
    if not success:
        pk_delete_logger.error("Failed to delete private key",
                              event_type="private_key_deletion_failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete private key"
        )
    
    pk_delete_logger.info("Private key deleted successfully",
                         event_type="private_key_deleted")
    return {"message": "Private key deleted successfully"}

# --- Delegation Endpoints --- 
@router.post("/delegation", response_model=delegation_schemas.DelegationRead)
async def store_delegation(
    delegation_in: delegation_schemas.DelegationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Allows an authenticated user to store a signed delegation.
    The frontend should construct and sign the delegation using the Gator SDK.
    """
    delegation_logger = logger.bind(endpoint="store_delegation", user_id=current_user.id)
    delegation_logger.info("Storing delegation for user",
                          delegate_address=delegation_in.delegate_address,
                          event_type="delegation_store_start")
    
    if delegation_in.delegate_address != settings.GATEWAY_DELEGATE_ADDRESS:
        delegation_logger.warning("Invalid delegation address",
                                 provided_address=delegation_in.delegate_address,
                                 expected_address=settings.GATEWAY_DELEGATE_ADDRESS,
                                 event_type="delegation_address_invalid")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Delegation must be granted to the configured gateway address: {settings.GATEWAY_DELEGATE_ADDRESS}"
        )

    existing_active = await delegation_crud.get_active_delegation_by_user(db, user_id=current_user.id)
    if existing_active:
        delegation_logger.info("Deactivating existing delegation",
                              existing_delegation_id=existing_active.id,
                              event_type="existing_delegation_deactivated")
        await delegation_crud.set_delegation_inactive(db, db_delegation=existing_active)

    db_delegation = await delegation_crud.create_user_delegation(
        db=db, delegation=delegation_in, user_id=current_user.id
    )
    
    delegation_logger.info("Delegation stored successfully",
                          delegation_id=db_delegation.id,
                          delegate_address=db_delegation.delegate_address,
                          event_type="delegation_stored")
    return db_delegation

@router.get("/delegation", response_model=List[delegation_schemas.DelegationRead])
async def get_user_delegations(
    skip: int = 0,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Retrieves the user's stored delegations.
    """
    list_delegations_logger = logger.bind(endpoint="get_user_delegations", user_id=current_user.id)
    list_delegations_logger.info("Retrieving user delegations",
                                skip=skip,
                                limit=limit,
                                event_type="delegations_list_start")
    
    delegations = await delegation_crud.get_delegations_by_user(db, user_id=current_user.id, skip=skip, limit=limit)
    
    list_delegations_logger.info("Retrieved user delegations successfully",
                                delegation_count=len(delegations),
                                event_type="delegations_listed")
    return delegations

@router.get("/delegation/active", response_model=Optional[delegation_schemas.DelegationRead])
async def get_active_user_delegation(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Retrieves the user's currently active delegation, if any.
    """
    active_delegation_logger = logger.bind(endpoint="get_active_user_delegation", user_id=current_user.id)
    active_delegation_logger.debug("Retrieving active delegation", event_type="active_delegation_lookup")
    
    delegation = await delegation_crud.get_active_delegation_by_user(db, user_id=current_user.id)
    
    active_delegation_logger.info("Active delegation lookup completed",
                                 has_active_delegation=delegation is not None,
                                 delegation_id=delegation.id if delegation else None,
                                 event_type="active_delegation_retrieved")
    return delegation

@router.delete("/delegation/{delegation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_delegation(
    delegation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Deletes a specific delegation for the user.
    Alternatively, could just mark it inactive.
    """
    delete_delegation_logger = logger.bind(endpoint="delete_delegation", 
                                          user_id=current_user.id, 
                                          delegation_id=delegation_id)
    delete_delegation_logger.info("Attempting to delete delegation",
                                 delegation_id=delegation_id,
                                 event_type="delegation_deletion_start")
    
    db_delegation = await delegation_crud.get_delegation(db, delegation_id=delegation_id)
    if not db_delegation or db_delegation.user_id != current_user.id:
        delete_delegation_logger.warning("Delegation not found for deletion",
                                        delegation_id=delegation_id,
                                        event_type="delegation_not_found")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delegation not found")

    # Using hard delete for now
    await delegation_crud.delete_delegation(db, db_delegation=db_delegation)
    
    delete_delegation_logger.info("Delegation deleted successfully",
                                 delegation_id=delegation_id,
                                 event_type="delegation_deleted")

    return Response(status_code=status.HTTP_204_NO_CONTENT)
# --- End Delegation Endpoints ---

@router.delete("/register", response_model=UserDeletionResponse, status_code=status.HTTP_200_OK)
async def delete_user_account(
    current_user: User = Depends(get_current_user),
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
    
    delete_user_logger = logger.bind(endpoint="delete_user_account", user_id=user_id)
    delete_user_logger.info("Starting user account deletion",
                           cognito_user_id=cognito_user_id,
                           event_type="user_deletion_start")
    
    try:
        # 1. Delete all sessions first (to avoid foreign key constraint violations)
        delete_user_logger.info("Deleting user sessions", event_type="user_sessions_deletion_start")
        sessions_deleted = await session_crud.delete_all_user_sessions(db, user_id)
        delete_user_logger.info("User sessions deleted",
                               sessions_deleted=sessions_deleted,
                               event_type="user_sessions_deleted")
        
        # 2. Delete all API keys manually (no cascade relationship)
        delete_user_logger.info("Deleting user API keys", event_type="user_api_keys_deletion_start")
        api_keys_deleted = await api_key_crud.delete_all_user_api_keys(db, user_id)
        delete_user_logger.info("User API keys deleted",
                               api_keys_deleted=api_keys_deleted,
                               event_type="user_api_keys_deleted")
        
        # 3. Delete the user (this will cascade delete private keys, automation settings, and delegations)
        delete_user_logger.info("Deleting user record", event_type="user_record_deletion_start")
        deleted_user = await user_crud.delete_user(db, user_id)
        
        if not deleted_user:
            delete_user_logger.error("User not found for deletion",
                                    event_type="user_not_found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        delete_user_logger.info("User record deleted successfully",
                               event_type="user_record_deleted")
        
        # 4. Delete the user from Cognito User Pool
        delete_user_logger.info("Deleting user from Cognito",
                               cognito_user_id=cognito_user_id,
                               event_type="cognito_deletion_start")
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
            delete_user_logger.info("User account deletion completed successfully",
                                   cognito_deletion_success=True,
                                   event_type="user_deletion_complete")
        else:
            message = "User account deleted from database, but Cognito deletion failed"
            delete_user_logger.warning("User account deletion partially successful",
                                     cognito_deletion_success=False,
                                     cognito_error=cognito_deletion_result.get("error"),
                                     event_type="user_deletion_partial")
        
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
        # Log the error properly with structured logging
        delete_user_logger.error("Error deleting user account",
                                error=str(e),
                                user_id=user_id,
                                cognito_user_id=cognito_user_id,
                                event_type="user_deletion_error",
                                exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete user account"
        )

@router.get("/keys/first", response_model=Optional[APIKeyDB])
async def get_first_api_key(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get the first (oldest) active API key for the current user.
    This is used for automatic API key selection on login.
    
    Requires JWT Bearer authentication with the token received from the login endpoint.
    """
    first_api_key = await api_key_crud.get_first_active_api_key(db, current_user.id)
    return first_api_key

@router.get("/keys/default", response_model=Optional[APIKeyDB])
async def get_default_api_key(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get the user's default API key. If no default is set, returns the first (oldest) active API key.
    This respects user preference for default key selection.
    
    Requires JWT Bearer authentication with the token received from the login endpoint.
    """
    default_api_key = await api_key_crud.get_default_api_key(db, current_user.id)
    return default_api_key

@router.put("/keys/{key_id}/default", response_model=APIKeyDB)
async def set_default_api_key(
    key_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Set an API key as the user's default. Clears any existing default.
    
    Requires JWT Bearer authentication with the token received from the login endpoint.
    """
    updated_key = await api_key_crud.set_default_api_key(db, key_id, current_user.id)
    
    if not updated_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found or not owned by user"
        )
    
    return updated_key

@router.get("/keys/default/decrypted")
async def get_default_api_key_decrypted(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get the user's default API key with the full decrypted key for auto-selection.
    
    This endpoint returns the full API key to enable seamless Chat/Test access.
    The key is decrypted using the user's Cognito data for security.
    
    Requires JWT Bearer authentication with the token received from the login endpoint.
    """
    api_key_obj, decrypted_key, status = await api_key_crud.get_decrypted_default_api_key(db, current_user.id)
    
    if status == "no_key_found":
        return {
            "error": "No default API key found",
            "error_code": "NO_DEFAULT_KEY",
            "message": "You don't have any API keys set as default. Please create an API key or set one as default.",
            "suggestion": "Create a new API key using POST /api/v1/auth/keys"
        }
    
    if status == "decryption_failed":
        return {
            "error": "API key decryption failed",
            "error_code": "DECRYPTION_FAILED", 
            "message": "Your default API key was found but could not be decrypted. This may be due to user data changes.",
            "key_info": {
                "id": api_key_obj.id,
                "key_prefix": api_key_obj.key_prefix,
                "name": api_key_obj.name,
                "created_at": api_key_obj.created_at
            },
            "suggestion": "Try refreshing your user data with GET /api/v1/auth/me?refresh_from_cognito=true, or create a new API key"
        }
    
    # Success case
    return {
        "id": api_key_obj.id,
        "key_prefix": api_key_obj.key_prefix,
        "name": api_key_obj.name,
        "is_default": api_key_obj.is_default,
        "created_at": api_key_obj.created_at,
        "full_key": decrypted_key  # The decrypted full API key
    }

# Export router
auth_router = router 