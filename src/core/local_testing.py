"""
Local Testing Utilities

Provides authentication bypass and mock services for local development.
Only active when BYPASS_COGNITO_AUTH=true and LOCAL_TESTING_MODE=true.
"""

import os
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.models import User
from src.crud import user as user_crud

logger = logging.getLogger(__name__)

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
        logger.info("✅ Created test user for local development")
    
    return test_user

def log_local_testing_status():
    """Log the current local testing configuration."""
    if is_local_testing_mode():
        logger.warning("🧪 LOCAL TESTING MODE ACTIVE")
        logger.warning("🔓 Cognito authentication BYPASSED")
        logger.warning("👤 Using test user: test@local.dev")
        logger.warning("⚠️  NOT FOR PRODUCTION USE")
    else:
        logger.info("🔒 Production authentication mode active")
