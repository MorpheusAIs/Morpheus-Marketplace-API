"""
Local Testing Utilities

Provides authentication bypass and mock services for local development.
Only active when BYPASS_COGNITO_AUTH=true and LOCAL_TESTING_MODE=true.
"""

import os
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.models import User
from src.crud import user as user_crud
from src.core.logging_config import get_core_logger

logger = get_core_logger()

def is_local_testing_mode() -> bool:
    """Check if we're in local testing mode."""
    return (
        os.getenv("LOCAL_TESTING_MODE", "false").lower() == "true" and
        os.getenv("BYPASS_COGNITO_AUTH", "false").lower() == "true"
    )

async def get_or_create_test_user(db: AsyncSession) -> User:
    """
    Get or create a test user for local development.
    Only works in local testing mode.
    """
    if not is_local_testing_mode():
        raise RuntimeError("Test user creation only available in local testing mode")
    
    # Try to get existing test user
    test_user = await user_crud.get_user_by_cognito_id(db, "local-test-user")
    
    if not test_user:
        # Create test user
        user_data = {
            'cognito_user_id': 'local-test-user',
            'email': 'test@local.dev',
            'name': 'Local Test User'
        }
        test_user = await user_crud.create_user_from_cognito(db, user_data)
        logger.info("Created test user for local development",
                   test_user_id=test_user.id,
                   test_email=user_data['email'],
                   event_type="test_user_created")
    
    return test_user

def log_local_testing_status():
    """Log the current local testing configuration."""
    if is_local_testing_mode():
        logger.warning("LOCAL TESTING MODE ACTIVE",
                      bypass_cognito=True,
                      test_user_email="test@local.dev",
                      production_safe=False,
                      event_type="local_testing_active")
        logger.warning("Cognito authentication BYPASSED - NOT FOR PRODUCTION USE",
                      event_type="local_testing_warning")
    else:
        logger.info("Production authentication mode active",
                   local_testing_enabled=False,
                   event_type="production_auth_active")
