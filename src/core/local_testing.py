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

# Environments where the Cognito auth bypass is permitted. Anything outside this
# set (production, staging, or an unrecognized value) must never run with the
# bypass active.
_BYPASS_ALLOWED_ENVIRONMENTS = {"development", "dev", "test", "local"}


class AuthBypassMisconfiguration(RuntimeError):
    """Raised when the auth bypass is enabled outside a local/test environment."""


def is_local_testing_mode() -> bool:
    """Check if we're in local testing mode.

    The bypass is only honored in explicitly local/test environments. If the
    bypass flags are set in any other environment (production, staging, or an
    unknown value) this raises ``AuthBypassMisconfiguration`` so the process
    fails loudly at startup instead of silently serving every request as a
    shared anonymous test user.
    """
    bypass_requested = (
        os.getenv("LOCAL_TESTING_MODE", "false").lower() == "true" and
        os.getenv("BYPASS_COGNITO_AUTH", "false").lower() == "true"
    )

    if not bypass_requested:
        return False

    environment = os.getenv("ENVIRONMENT", "development").lower()
    if environment not in _BYPASS_ALLOWED_ENVIRONMENTS:
        raise AuthBypassMisconfiguration(
            "Cognito auth bypass (LOCAL_TESTING_MODE + BYPASS_COGNITO_AUTH) is "
            f"enabled while ENVIRONMENT={environment!r}. The bypass is only "
            f"permitted in {sorted(_BYPASS_ALLOWED_ENVIRONMENTS)}. Refusing to "
            "run with authentication disabled."
        )

    return True

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
        test_user = await user_crud.create_user_from_cognito(db, 'local-test-user')
        logger.info("Created test user for local development",
                   test_user_id=test_user.id,
                   event_type="test_user_created")
    
    return test_user

def log_local_testing_status():
    """Log the current local testing configuration.

    Called during application startup. If the auth bypass is misconfigured for
    the current environment, ``is_local_testing_mode`` raises and this logs a
    critical error before re-raising, aborting startup.
    """
    try:
        local_testing_active = is_local_testing_mode()
    except AuthBypassMisconfiguration as exc:
        logger.critical(
            "Refusing to start: authentication bypass enabled outside a "
            "local/test environment",
            error=str(exc),
            event_type="local_testing_misconfiguration",
        )
        raise

    if local_testing_active:
        logger.warning("LOCAL TESTING MODE ACTIVE",
                      bypass_cognito=True,
                      test_cognito_id="local-test-user",
                      production_safe=False,
                      event_type="local_testing_active")
        logger.warning("Cognito authentication BYPASSED - NOT FOR PRODUCTION USE",
                      event_type="local_testing_warning")
    else:
        logger.info("Production authentication mode active",
                   local_testing_enabled=False,
                   event_type="production_auth_active")
