"""Voiding a hold gives back its staking part only within the staking period it came from.

The daily staking reset sets staking_available to the full daily amount and
ignores pending holds. A video hold can span that reset (up to 6 h), so giving
its staking part back after the reset would roll staking credit over to the
next day. The paid part is always given back.
"""

import os
import uuid
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/test_db",
)

import pytest

from src.db.models import LedgerStatus
from src.schemas.billing import UsageVoidRequest
from src.services import billing_service as billing_module
from src.services.billing_service import BillingService

USER_ID = 7
TODAY = date(2026, 11, 6)
RESET_AT = datetime(2026, 11, 6, 0, 5, 3)  # the 00:05 UTC daily sync
HELD_PAID = Decimal("-1.50000000")
HELD_STAKING = Decimal("-2.50000000")


@pytest.mark.parametrize(
    "hold_created_at, refresh_date, reset_at, held_staking, staking_back",
    [
        # Placed after today's reset: its staking part is still out
        (datetime(2026, 11, 6, 9, 0), TODAY, RESET_AT, HELD_STAKING, Decimal("2.50000000")),
        # Placed yesterday, the reset already restored the allowance
        (datetime(2026, 11, 5, 23, 58), TODAY, RESET_AT, HELD_STAKING, Decimal("0")),
        # Placed today between midnight and the reset
        (datetime(2026, 11, 6, 0, 2), TODAY, RESET_AT, HELD_STAKING, Decimal("0")),
        # Today's sync has not run yet: still yesterday's period
        (datetime(2026, 11, 6, 0, 2), date(2026, 11, 5), datetime(2026, 11, 5, 0, 5), HELD_STAKING,
         Decimal("2.50000000")),
        # A reset without a ledger entry (zero daily amount) still counts
        (datetime(2026, 11, 6, 0, 2), TODAY, None, HELD_STAKING, Decimal("0")),
        (datetime(2026, 11, 7, 0, 2), TODAY, None, HELD_STAKING, Decimal("2.50000000")),
        # No staking part: nothing to decide
        (datetime(2026, 11, 5, 23, 58), TODAY, RESET_AT, Decimal("0"), Decimal("0")),
    ],
)
@pytest.mark.asyncio
async def test_video_void_gives_staking_back_only_within_its_period(
    hold_created_at, refresh_date, reset_at, held_staking, staking_back,
):
    hold = SimpleNamespace(
        id=uuid.uuid4(), user_id=USER_ID, status=LedgerStatus.pending,
        amount_paid=HELD_PAID, amount_staking=held_staking, created_at=hold_created_at,
    )
    balance = SimpleNamespace(staking_refresh_date=refresh_date)
    update_balance = AsyncMock()
    db = MagicMock(commit=AsyncMock(), refresh=AsyncMock())
    crud = billing_module.credits_crud
    with patch.object(crud, "get_ledger_entry_by_id", AsyncMock(return_value=hold)), \
            patch.object(crud, "update_ledger_entry", AsyncMock(return_value=hold)), \
            patch.object(crud, "get_or_create_balance", AsyncMock(return_value=balance)), \
            patch.object(crud, "get_staking_reset_at", AsyncMock(return_value=reset_at)), \
            patch.object(crud, "update_balance", update_balance):
        result = await BillingService(pricing_service=MagicMock()).void_usage(
            db, USER_ID,
            UsageVoidRequest(ledger_entry_id=hold.id, failure_code="job_failed", failure_reason="x"),
        )

    assert result.voided
    released = update_balance.await_args.kwargs
    assert released["paid_holds_delta"] == -HELD_PAID
    assert released["staking_delta"] == staking_back
