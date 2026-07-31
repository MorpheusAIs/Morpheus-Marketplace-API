"""
Regression tests for cross-user access control on the Coinbase payment-link
lookup endpoint (BOLA/IDOR).

The GET /billing/coinbase/payment-links/{id} handler must only return a link
whose ``metadata.user_id`` matches the authenticated caller's Cognito id.
Any other id (including one that exists but belongs to another user) must
return 404 so ownership cannot be probed by enumerating ids.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.api.v1.billing import coinbase as coinbase_api


def _user(cognito_user_id: str, user_id: int = 1) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.cognito_user_id = cognito_user_id
    return user


def _link(owner_cognito_id):
    metadata = {"user_id": owner_cognito_id} if owner_cognito_id is not None else {}
    return {
        "id": "6a54d6b6ec41fe24d35f3c43",
        "url": "https://payments.coinbase.com/payment-links/pl_x",
        "status": "ACTIVE",
        "amount": "42.00",
        "currency": "USDC",
        "metadata": metadata,
    }


@pytest.mark.asyncio
async def test_owner_can_read_own_payment_link():
    """The user who created the link (matching metadata.user_id) gets it back."""
    owner = _user("owner-sub-123")
    with patch.object(
        coinbase_api.coinbase_payment_link_service,
        "get_payment_link",
        AsyncMock(return_value=_link("owner-sub-123")),
    ):
        result = await coinbase_api.get_payment_link(
            payment_link_id="6a54d6b6ec41fe24d35f3c43",
            current_user=owner,
        )

    assert result["id"] == "6a54d6b6ec41fe24d35f3c43"
    assert result["metadata"]["user_id"] == "owner-sub-123"


@pytest.mark.asyncio
async def test_other_user_gets_404_not_the_record():
    """A different authenticated user must not be able to read the link."""
    attacker = _user("attacker-sub-999", user_id=2)
    with patch.object(
        coinbase_api.coinbase_payment_link_service,
        "get_payment_link",
        AsyncMock(return_value=_link("owner-sub-123")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await coinbase_api.get_payment_link(
                payment_link_id="6a54d6b6ec41fe24d35f3c43",
                current_user=attacker,
            )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_link_without_owner_metadata_is_denied():
    """A link lacking metadata.user_id cannot be claimed by any caller."""
    caller = _user("someone-sub-123")
    with patch.object(
        coinbase_api.coinbase_payment_link_service,
        "get_payment_link",
        AsyncMock(return_value=_link(None)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await coinbase_api.get_payment_link(
                payment_link_id="6a54d6b6ec41fe24d35f3c43",
                current_user=caller,
            )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
