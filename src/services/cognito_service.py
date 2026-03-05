"""
Cognito User Management Service

Handles interactions with AWS Cognito User Pools for user lifecycle management.
Uses the ECS task role for admin operations (AdminDeleteUser) and the user's
own access token for user-level operations (GetUser).
"""

import boto3
from typing import Dict, Any, Optional
from botocore.exceptions import ClientError, NoCredentialsError

from src.core.config import settings
from src.core.logging_config import get_auth_logger

logger = get_auth_logger()

class CognitoUserService:
    """Service for managing Cognito users."""
    
    def __init__(self):
        try:
            self.cognito_client = boto3.client(
                'cognito-idp',
                region_name=settings.COGNITO_REGION,
            )
            logger.info("Cognito client initialized (using task role credentials)",
                       cognito_region=settings.COGNITO_REGION,
                       user_pool_id=settings.COGNITO_USER_POOL_ID,
                       event_type="cognito_client_init_success")
        except NoCredentialsError:
            logger.error("AWS credentials not found for Cognito operations",
                        cognito_region=settings.COGNITO_REGION,
                        event_type="cognito_credentials_error")
            raise
        except Exception as e:
            logger.error("Failed to initialize Cognito client",
                        error=str(e),
                        cognito_region=settings.COGNITO_REGION,
                        event_type="cognito_client_init_error")
            raise

    async def get_user_by_token(self, access_token: str) -> Optional[Dict[str, Any]]:
        """
        Fetch user attributes from Cognito using the user's own access token.
        This mirrors what the frontend does with GetUserCommand — no admin
        permissions needed, just the authenticated user's token.

        Returns dict with 'email' and 'name' keys, or None on failure.
        """
        try:
            response = self.cognito_client.get_user(AccessToken=access_token)
            attrs = {a['Name']: a['Value'] for a in response.get('UserAttributes', [])}
            return {
                'email': attrs.get('email'),
                'name': attrs.get('name') or attrs.get('given_name'),
                'cognito_user_id': attrs.get('sub'),
            }
        except ClientError as e:
            error_code = e.response['Error']['Code']
            logger.warning("Cognito GetUser failed",
                          error_code=error_code,
                          event_type="cognito_get_user_error")
            return None
        except Exception as e:
            logger.warning("Unexpected error fetching Cognito user",
                          error=str(e),
                          event_type="cognito_get_user_unexpected_error")
            return None

    async def delete_user(self, cognito_user_id: str) -> Dict[str, Any]:
        """
        Delete a user from Cognito User Pool (admin operation using task role).
        """
        try:
            delete_logger = logger.bind(cognito_user_id=cognito_user_id)
            delete_logger.info("Attempting to delete Cognito user",
                              user_pool_id=settings.COGNITO_USER_POOL_ID,
                              event_type="cognito_user_deletion_start")
            
            response = self.cognito_client.admin_delete_user(
                UserPoolId=settings.COGNITO_USER_POOL_ID,
                Username=cognito_user_id
            )
            
            delete_logger.info("Successfully deleted Cognito user",
                              event_type="cognito_user_deleted")
            
            return {
                "success": True,
                "cognito_user_id": cognito_user_id,
                "message": "User successfully deleted from Cognito",
                "response_metadata": response.get('ResponseMetadata', {})
            }
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            
            if error_code == 'UserNotFoundException':
                delete_logger.warning("Cognito user not found for deletion",
                                     error_code=error_code,
                                     event_type="cognito_user_not_found")
                return {
                    "success": True,
                    "cognito_user_id": cognito_user_id,
                    "message": "User not found in Cognito (may have been already deleted)",
                    "warning": True
                }
            
            elif error_code == 'InvalidParameterException':
                delete_logger.error("Invalid parameter for Cognito deletion",
                                   error_code=error_code,
                                   error_message=error_message,
                                   event_type="cognito_invalid_parameter")
                return {
                    "success": False,
                    "cognito_user_id": cognito_user_id,
                    "error": f"Invalid parameter: {error_message}",
                    "error_code": error_code
                }
                
            else:
                delete_logger.error("Cognito deletion failed",
                                   error_code=error_code,
                                   error_message=error_message,
                                   event_type="cognito_deletion_failed")
                return {
                    "success": False,
                    "cognito_user_id": cognito_user_id,
                    "error": f"Cognito deletion failed: {error_message}",
                    "error_code": error_code
                }
                
        except Exception as e:
            delete_logger.error("Unexpected error during Cognito deletion",
                               error=str(e),
                               event_type="cognito_deletion_unexpected_error")
            return {
                "success": False,
                "cognito_user_id": cognito_user_id,
                "error": f"Unexpected error: {str(e)}",
                "error_code": "UnknownError"
            }

cognito_service = CognitoUserService()
