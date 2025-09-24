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
from .structured_logger import AUTH_LOG

# Setup structured logging (Authentication category)
local_testing_log = AUTH_LOG.named("LOCAL_TESTING")

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
        local_testing_log.with_fields(
            event_type="test_user_creation",
            email="test@local.dev",
            environment="local_development"
        ).info("Created test user for local development")
    
    return test_user

def log_local_testing_status():
    """Log the current local testing configuration."""
    if is_local_testing_mode():
        local_testing_log.with_fields(
            event_type="local_testing_mode",
            mode="active",
            security_warning=True,
            authentication="bypassed",
            test_user="test@local.dev"
        ).warn("LOCAL TESTING MODE ACTIVE")
        local_testing_log.with_fields(
            event_type="authentication_bypass",
            security_warning=True
        ).warn("Cognito authentication BYPASSED")
        local_testing_log.with_fields(
            event_type="test_user_info",
            test_user="test@local.dev"
        ).warn("Using test user: test@local.dev")
        local_testing_log.with_fields(
            event_type="production_warning",
            security_warning=True
        ).warn("NOT FOR PRODUCTION USE")
    else:
        local_testing_log.with_fields(
            event_type="authentication_mode",
            mode="production",
            security_status="active"
        ).info("Production authentication mode active")
