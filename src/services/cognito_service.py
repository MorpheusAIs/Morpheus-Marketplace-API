"""
Cognito User Management Service

Handles interactions with AWS Cognito User Pools for user lifecycle management.
"""

import boto3
from typing import Optional, Dict, Any
from botocore.exceptions import ClientError, NoCredentialsError

from src.core.config import settings
from ..core.structured_logger import AUTH_LOG

# Setup structured logging (Auth category)
cognito_log = AUTH_LOG.named("COGNITO")

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
            cognito_log.with_fields(
                event_type="cognito_initialization",
                region=settings.COGNITO_REGION,
                status="success"
            ).infof("Cognito client initialized for region: %s", settings.COGNITO_REGION)
        except NoCredentialsError:
            cognito_log.with_fields(
                event_type="cognito_initialization",
                status="failed",
                error="no_credentials"
            ).error("AWS credentials not found for Cognito operations")
            raise
        except Exception as e:
            cognito_log.with_fields(
                event_type="cognito_initialization",
                status="failed",
                error=str(e)
            ).errorf("Failed to initialize Cognito client: %s", str(e))
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
            cognito_log.with_fields(
                event_type="user_deletion",
                cognito_user_id=cognito_user_id,
                status="attempting"
            ).infof("Attempting to delete Cognito user: %s", cognito_user_id)
            
            # Delete the user from Cognito User Pool
            response = self.cognito_client.admin_delete_user(
                UserPoolId=settings.COGNITO_USER_POOL_ID,
                Username=cognito_user_id
            )
            
            cognito_log.with_fields(
                event_type="user_deletion",
                cognito_user_id=cognito_user_id,
                status="success"
            ).infof("Successfully deleted Cognito user: %s", cognito_user_id)
            
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
                cognito_log.with_fields(
                    event_type="user_deletion",
                    cognito_user_id=cognito_user_id,
                    status="not_found",
                    error_code=error_code
                ).warnf("Cognito user not found: %s", cognito_user_id)
                return {
                    "success": True,  # Consider this successful since user doesn't exist
                    "cognito_user_id": cognito_user_id,
                    "message": "User not found in Cognito (may have been already deleted)",
                    "warning": True
                }
            
            elif error_code == 'InvalidParameterException':
                cognito_log.with_fields(
                    event_type="user_deletion",
                    cognito_user_id=cognito_user_id,
                    status="failed",
                    error_code=error_code,
                    error_message=error_message
                ).errorf("Invalid parameter for Cognito deletion: %s", error_message)
                return {
                    "success": False,
                    "cognito_user_id": cognito_user_id,
                    "error": f"Invalid parameter: {error_message}",
                    "error_code": error_code
                }
                
            else:
                cognito_log.with_fields(
                    event_type="user_deletion",
                    cognito_user_id=cognito_user_id,
                    status="failed",
                    error_code=error_code,
                    error_message=error_message
                ).errorf("Cognito deletion failed: %s - %s", error_code, error_message)
                return {
                    "success": False,
                    "cognito_user_id": cognito_user_id,
                    "error": f"Cognito deletion failed: {error_message}",
                    "error_code": error_code
                }
                
        except Exception as e:
            cognito_log.with_fields(
                event_type="user_deletion",
                cognito_user_id=cognito_user_id,
                status="failed",
                error=str(e)
            ).errorf("Unexpected error during Cognito deletion: %s", str(e))
            return {
                "success": False,
                "cognito_user_id": cognito_user_id,
                "error": f"Unexpected error: {str(e)}",
                "error_code": "UnknownError"
            }
    
    async def get_user_info(self, cognito_user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get user information from Cognito
        
        Args:
            cognito_user_id: The Cognito user's sub (UUID)
            
        Returns:
            User information dict or None if not found
        """
        try:
            response = self.cognito_client.admin_get_user(
                UserPoolId=settings.COGNITO_USER_POOL_ID,
                Username=cognito_user_id
            )
            
            # Parse user attributes
            user_attributes = {}
            for attr in response.get('UserAttributes', []):
                user_attributes[attr['Name']] = attr['Value']
            
            return {
                "cognito_user_id": cognito_user_id,
                "username": response.get('Username'),
                "user_status": response.get('UserStatus'),
                "enabled": response.get('Enabled'),
                "user_create_date": response.get('UserCreateDate'),
                "user_last_modified_date": response.get('UserLastModifiedDate'),
                "attributes": user_attributes
            }
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'UserNotFoundException':
                return None
            cognito_log.with_fields(
                event_type="user_info_fetch",
                cognito_user_id=cognito_user_id,
                status="failed",
                error=str(e)
            ).errorf("Error fetching Cognito user info: %s", str(e))
            return None
        except Exception as e:
            cognito_log.with_fields(
                event_type="user_info_fetch",
                cognito_user_id=cognito_user_id,
                status="failed",
                error=str(e)
            ).errorf("Unexpected error fetching Cognito user info: %s", str(e))
            return None

# Global service instance
cognito_service = CognitoUserService() 