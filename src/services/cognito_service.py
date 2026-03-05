"""
Cognito User Management Service

Handles interactions with AWS Cognito User Pools for user lifecycle management.
"""

import boto3
from typing import Dict, Any
from botocore.exceptions import ClientError, NoCredentialsError

from src.core.config import settings
from src.core.logging_config import get_auth_logger

logger = get_auth_logger()

class CognitoUserService:
    """Service for managing Cognito users"""
    
    def __init__(self):
        """Initialize Cognito client"""
        try:
            # Create Cognito Identity Provider client
            self.cognito_client = boto3.client(
                'cognito-idp',
                region_name=settings.COGNITO_REGION,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                aws_session_token=settings.AWS_SESSION_TOKEN
            )
            logger.info("Cognito client initialized successfully",
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
    
    async def delete_user(self, cognito_user_id: str) -> Dict[str, Any]:
        """
        Delete a user from Cognito User Pool
        
        Args:
            cognito_user_id: The Cognito user's sub (UUID)
            
        Returns:
            Dict with deletion status and details
        """
        try:
            delete_logger = logger.bind(cognito_user_id=cognito_user_id)
            delete_logger.info("Attempting to delete Cognito user",
                              cognito_user_id=cognito_user_id,
                              user_pool_id=settings.COGNITO_USER_POOL_ID,
                              event_type="cognito_user_deletion_start")
            
            # Delete the user from Cognito User Pool
            response = self.cognito_client.admin_delete_user(
                UserPoolId=settings.COGNITO_USER_POOL_ID,
                Username=cognito_user_id
            )
            
            delete_logger.info("Successfully deleted Cognito user",
                              cognito_user_id=cognito_user_id,
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
                                     cognito_user_id=cognito_user_id,
                                     error_code=error_code,
                                     event_type="cognito_user_not_found")
                return {
                    "success": True,  # Consider this successful since user doesn't exist
                    "cognito_user_id": cognito_user_id,
                    "message": "User not found in Cognito (may have been already deleted)",
                    "warning": True
                }
            
            elif error_code == 'InvalidParameterException':
                delete_logger.error("Invalid parameter for Cognito deletion",
                                   cognito_user_id=cognito_user_id,
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
                                   cognito_user_id=cognito_user_id,
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
                               cognito_user_id=cognito_user_id,
                               error=str(e),
                               event_type="cognito_deletion_unexpected_error")
            return {
                "success": False,
                "cognito_user_id": cognito_user_id,
                "error": f"Unexpected error: {str(e)}",
                "error_code": "UnknownError"
            }
    
# Global service instance
cognito_service = CognitoUserService() 