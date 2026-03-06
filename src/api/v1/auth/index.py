# Authentication routes 
from typing import List, Any, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status, Depends, Body, Request, Response, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from ....crud import user as user_crud
from ....crud import api_key as api_key_crud
from ....db.database import get_db, get_db_session
from ....schemas.user import UserDeletionResponse, AgeVerificationRequest
from ....schemas.api_key import APIKeyCreate, APIKeyResponse, APIKeyDB
from ....dependencies import CurrentUser, get_current_user
from ....db.models import User
from ....services.cognito_service import cognito_service
from ....core.config import settings
from ....core.logging_config import get_auth_logger

logger = get_auth_logger()

router = APIRouter(tags=["Auth"])

_bearer = HTTPBearer(auto_error=False)

@router.get("/me", response_model=dict)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
    token: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
):
    """
    Get current user information.

    Email and name are fetched live from Cognito (not stored in DB).
    Uses the caller's own access token — same pattern as the frontend.
    """
    email = None
    name = None

    if token:
        cognito_info = await cognito_service.get_user_by_token(token.credentials)
        if cognito_info:
            email = cognito_info.get('email')
            name = cognito_info.get('name')

    return {
        "id": current_user.id,
        "cognito_user_id": current_user.cognito_user_id,
        "email": email,
        "name": name,
        "is_active": current_user.is_active,
        "age_verified": current_user.age_verified,
        "age_verified_at": current_user.age_verified_at,
        "created_at": current_user.created_at,
        "updated_at": current_user.updated_at,
        "data_source": "cognito_live",
    }

@router.post("/verify-age", response_model=dict)
async def verify_age(
    body: AgeVerificationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Submit age verification consent (18+).

    The user must send `{"age_verified": true}` to confirm they are 18 or older.
    The confirmation and the timestamp are stored as evidence of consent.

    Requires JWT Bearer authentication with Cognito token.
    """
    verify_logger = logger.bind(endpoint="verify_age", user_id=current_user.id)

    if not body.age_verified:
        verify_logger.warning("Age verification rejected (false)",
                             event_type="age_verification_rejected")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Age verification must be confirmed as true"
        )

    updated_user = await user_crud.set_age_verification(db, current_user.id, True)

    if not updated_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    verify_logger.info("Age verification confirmed",
                      age_verified_at=updated_user.age_verified_at.isoformat(),
                      event_type="age_verification_confirmed")

    return {
        "age_verified": updated_user.age_verified,
        "age_verified_at": updated_user.age_verified_at
    }

@router.post("/keys", response_model=APIKeyResponse)
async def create_api_key(
    api_key_in: APIKeyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
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
    db: AsyncSession = Depends(get_db_session)
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
    db: AsyncSession = Depends(get_db_session)
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

@router.delete("/register", response_model=UserDeletionResponse, status_code=status.HTTP_200_OK)
async def delete_user_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Delete the current user's account and all associated data.

    Always: deletes API keys, user record (cascades: wallet_links, chats, etc.).
    Cognito: only in production (ENVIRONMENT in production|prod|prd) do we delete
    the Cognito user. In dev/test/non-prod we leave the Cognito identity intact so
    "Delete account" in TEST cannot accidentally nuke the user's real identity.

    Requires JWT Bearer authentication.
    """
    user_id = current_user.id
    cognito_user_id = current_user.cognito_user_id
    
    delete_user_logger = logger.bind(endpoint="delete_user_account", user_id=user_id)
    delete_user_logger.info("Starting user account deletion",
                           cognito_user_id=cognito_user_id,
                           event_type="user_deletion_start")
    
    try:
        # 1. Delete all API keys manually (no cascade relationship)
        delete_user_logger.info("Deleting user API keys", event_type="user_api_keys_deletion_start")
        api_keys_deleted = await api_key_crud.delete_all_user_api_keys(db, user_id)
        delete_user_logger.info("User API keys deleted",
                               api_keys_deleted=api_keys_deleted,
                               event_type="user_api_keys_deleted")
        
        # 2. Delete the user (this will cascade delete related data)
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

        # 3. Delete Cognito user only in production; in dev/test leave identity intact
        env_lower = (settings.ENVIRONMENT or "").strip().lower()
        is_production = env_lower in ("production", "prod", "prd")
        if is_production:
            delete_user_logger.info("Deleting user from Cognito (production)",
                                   cognito_user_id=cognito_user_id,
                                   event_type="cognito_deletion_start")
            cognito_deletion_result = await cognito_service.delete_user(cognito_user_id)
        else:
            delete_user_logger.info("Skipping Cognito delete (non-production); identity preserved",
                                   environment=settings.ENVIRONMENT,
                                   event_type="cognito_deletion_skipped")
            cognito_deletion_result = {
                "success": True,
                "skipped": True,
                "reason": "non_production",
            }

        # Prepare response data
        deleted_data = {
            "api_keys": api_keys_deleted,
            "wallet_links": True  # Will be deleted via cascade if any exist
        }

        # Use timezone-aware datetime and convert to naive for consistency
        deleted_at_with_tz = datetime.now(timezone.utc)
        deleted_at = deleted_at_with_tz.replace(tzinfo=None)

        # Determine overall success message
        if cognito_deletion_result.get("skipped"):
            message = "User account deleted from database. Cognito identity preserved (non-production)."
            delete_user_logger.info("User account deletion completed (Cognito skipped)",
                                   event_type="user_deletion_complete")
        elif cognito_deletion_result["success"]:
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
    db: AsyncSession = Depends(get_db_session)
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
    db: AsyncSession = Depends(get_db_session)
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
    db: AsyncSession = Depends(get_db_session)
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
    db: AsyncSession = Depends(get_db_session)
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
            "suggestion": "Try creating a new API key using POST /api/v1/auth/keys"
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