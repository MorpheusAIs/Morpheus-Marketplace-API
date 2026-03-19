"""
Coinbase Business Payment Link API service.

Provides CRUD operations for creating, listing, retrieving, and deactivating
payment links via the Coinbase Business REST API.

API Reference: https://docs.cdp.coinbase.com/api-reference/business-api/rest-api/payment-links/introduction
"""
from typing import Optional, Dict, Any

import httpx

from src.services.coinbase_auth import get_auth_headers, CDP_API_BASE_URL
from src.core.logging_config import get_core_logger

logger = get_core_logger()

# Payment Link API base path
PAYMENT_LINKS_PATH = "/api/v1/payment-links"

# Default timeout for API calls (seconds)
DEFAULT_TIMEOUT = 30.0


class CoinbasePaymentLinkService:
    """
    Service for managing Coinbase Business Payment Links.

    Payment links are single-use USDC payment URLs that can be shared
    with customers. Once paid, a webhook is fired and the link status
    transitions to COMPLETED.
    """

    async def create_payment_link(
        self,
        amount: str,
        currency: str = "USDC",
        metadata: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        success_redirect_url: Optional[str] = None,
        failure_redirect_url: Optional[str] = None,
        expires_at: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new payment link.

        Args:
            amount: Payment amount as a string (e.g., "100.00")
            currency: Currency code (currently only USDC supported)
            metadata: Optional key-value pairs passed through the payment flow
            description: Optional description shown on the payment page
            success_redirect_url: HTTPS URL to redirect on success
            failure_redirect_url: HTTPS URL to redirect on failure
            expires_at: ISO 8601 timestamp when the link expires (default: 1 year)
            idempotency_key: Optional idempotency key for the request

        Returns:
            Payment link response dict with id, url, status, etc.

        Raises:
            httpx.HTTPStatusError: On API error responses
            ValueError: If CDP credentials are not configured
        """
        path = PAYMENT_LINKS_PATH
        headers = get_auth_headers("POST", path)

        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key

        body: Dict[str, Any] = {
            "amount": amount,
            "currency": currency,
        }

        if metadata:
            body["metadata"] = metadata
        if description:
            body["description"] = description
        if success_redirect_url:
            body["successRedirectUrl"] = success_redirect_url
        if failure_redirect_url:
            body["failRedirectUrl"] = failure_redirect_url
        if expires_at:
            body["expiresAt"] = expires_at

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(
                f"{CDP_API_BASE_URL}{path}",
                headers=headers,
                json=body,
            )
            response.raise_for_status()

        result = response.json()
        logger.info(
            "Created Coinbase Payment Link",
            payment_link_id=result.get("id"),
            payment_link_url=result.get("url"),
            amount=amount,
            currency=currency,
        )
        return result

    async def list_payment_links(
        self,
        limit: int = 25,
        cursor: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List payment links with optional filtering and pagination.

        Args:
            limit: Max results per page (default 25)
            cursor: Pagination cursor from a previous response
            status: Filter by status (ACTIVE, COMPLETED, EXPIRED, DEACTIVATED)

        Returns:
            Paginated response with payment links and pagination info
        """
        path = PAYMENT_LINKS_PATH
        headers = get_auth_headers("GET", path)

        params: Dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                f"{CDP_API_BASE_URL}{path}",
                headers=headers,
                params=params,
            )
            response.raise_for_status()

        return response.json()

    async def get_payment_link(self, payment_link_id: str) -> Dict[str, Any]:
        """
        Retrieve a single payment link by ID.

        Args:
            payment_link_id: The 24-char hex payment link ID

        Returns:
            Payment link details
        """
        path = f"{PAYMENT_LINKS_PATH}/{payment_link_id}"
        headers = get_auth_headers("GET", path)

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                f"{CDP_API_BASE_URL}{path}",
                headers=headers,
            )
            response.raise_for_status()

        return response.json()

    async def deactivate_payment_link(self, payment_link_id: str) -> Dict[str, Any]:
        """
        Deactivate a payment link (prevents further payments).

        Args:
            payment_link_id: The 24-char hex payment link ID

        Returns:
            Updated payment link with DEACTIVATED status
        """
        path = f"{PAYMENT_LINKS_PATH}/{payment_link_id}/deactivate"
        headers = get_auth_headers("POST", path)

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(
                f"{CDP_API_BASE_URL}{path}",
                headers=headers,
            )
            response.raise_for_status()

        result = response.json()
        logger.info(
            "Deactivated Coinbase Payment Link",
            payment_link_id=payment_link_id,
            new_status=result.get("status"),
        )
        return result


# Singleton instance
coinbase_payment_link_service = CoinbasePaymentLinkService()
