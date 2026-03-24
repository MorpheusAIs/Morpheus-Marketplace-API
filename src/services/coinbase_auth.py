"""
CDP (Coinbase Developer Platform) API Key authentication.

Uses the CDP SDK to generate signed JWTs for authenticating with the
Coinbase Business API. Each request requires a unique JWT with the
target URI embedded in the payload.

Docs: https://docs.cdp.coinbase.com/api-reference/v2/authentication
Sandbox: https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/sandbox
"""
from cdp.auth.utils.jwt import generate_jwt, JwtOptions

from src.core.config import settings
from src.core.logging_config import get_core_logger

logger = get_core_logger()

CDP_API_HOST = "business.coinbase.com"
CDP_API_BASE_URL = f"https://{CDP_API_HOST}"

# Sandbox adds /sandbox before the API path; production uses no prefix.
CDP_PATH_PREFIX = "/sandbox" if settings.CDP_SANDBOX else ""


def _build_jwt(method: str, path: str) -> str:
    """
    Build a signed JWT for a CDP API request using the CDP SDK.

    The path should already include the sandbox prefix when applicable.

    Args:
        method: HTTP method (GET, POST, etc.)
        path: Full API path (e.g., /sandbox/api/v1/payment-links or /api/v1/payment-links)

    Returns:
        Signed JWT string

    Raises:
        ValueError: If CDP API key credentials are not configured
    """
    if not settings.CDP_API_KEY_ID or not settings.CDP_API_KEY_SECRET:
        raise ValueError(
            "CDP API credentials not configured. "
            "Set CDP_API_KEY_ID and CDP_API_KEY_SECRET environment variables."
        )

    return generate_jwt(JwtOptions(
        api_key_id=settings.CDP_API_KEY_ID,
        api_key_secret=settings.CDP_API_KEY_SECRET,
        request_method=method.upper(),
        request_host=CDP_API_HOST,
        request_path=path,
        expires_in=120,
    ))


def get_auth_headers(method: str, path: str) -> dict:
    """
    Get authentication headers for a CDP API request.

    Args:
        method: HTTP method
        path: Full API path (including sandbox prefix if applicable)

    Returns:
        Dict with Authorization header
    """
    token = _build_jwt(method, path)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
