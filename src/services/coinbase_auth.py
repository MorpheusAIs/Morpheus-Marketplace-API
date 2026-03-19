"""
CDP (Coinbase Developer Platform) API Key authentication.

Generates ES256-signed JWTs for authenticating with the Coinbase Business API.
Each request requires a unique JWT with the target URI embedded in the payload.

Docs: https://docs.cdp.coinbase.com/coinbase-business/authentication-authorization/api-key-authentication
"""
import time
import secrets

from jose import jwt

from src.core.config import settings
from src.core.logging_config import get_core_logger

logger = get_core_logger()

# JWT lifetime in seconds (Coinbase enforces max 2 minutes)
CDP_JWT_LIFETIME_SECONDS = 120

# Coinbase Business API base URL
CDP_API_BASE_URL = "https://api.coinbase.com"


def _build_jwt(method: str, path: str) -> str:
    """
    Build a signed JWT for a CDP API request.

    Args:
        method: HTTP method (GET, POST, etc.)
        path: API path (e.g., /api/v1/payment-links)

    Returns:
        Signed JWT string

    Raises:
        ValueError: If CDP API key credentials are not configured
    """
    if not settings.CDP_API_KEY_NAME or not settings.CDP_API_KEY_PRIVATE_KEY:
        raise ValueError(
            "CDP API credentials not configured. "
            "Set CDP_API_KEY_NAME and CDP_API_KEY_PRIVATE_KEY environment variables."
        )

    now = int(time.time())
    uri = f"{method.upper()} api.coinbase.com{path}"

    payload = {
        "sub": settings.CDP_API_KEY_NAME,
        "iss": "cdp",
        "nbf": now,
        "exp": now + CDP_JWT_LIFETIME_SECONDS,
        "uri": uri,
    }

    headers = {
        "alg": "ES256",
        "typ": "JWT",
        "kid": settings.CDP_API_KEY_NAME,
        "nonce": secrets.token_hex(16),
    }

    # CDP_API_KEY_PRIVATE_KEY is an EC PEM key.
    # Environment variables may have literal \n — replace with real newlines.
    private_key = settings.CDP_API_KEY_PRIVATE_KEY.replace("\\n", "\n")

    return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)


def get_auth_headers(method: str, path: str) -> dict:
    """
    Get authentication headers for a CDP API request.

    Args:
        method: HTTP method
        path: API path

    Returns:
        Dict with Authorization header
    """
    token = _build_jwt(method, path)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
