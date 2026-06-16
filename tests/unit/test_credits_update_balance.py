"""Unit tests for H3: credits.update_balance ensure_exists / return_balance flags.

Hot-path callers (hold/finalize/void) pass both flags False to skip the
redundant ensure-exists read and the post-update re-SELECT; defaults preserve
the previous behavior. The atomic server-side delta UPDATE is unchanged.
"""
import os
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.crud import credits as credits_crud


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


def _select_result(balance):
    r = MagicMock()
    r.scalar_one.return_value = balance
    return r


async def test_hotpath_skips_ensure_exists_and_reselect(mock_db):
    with patch("src.crud.credits.get_or_create_balance", new_callable=AsyncMock) as goc, \
         patch("src.crud.credits.cache_service.delete", new_callable=AsyncMock) as cdel:
        out = await credits_crud.update_balance(
            mock_db, user_id=1, paid_holds_delta=Decimal("-5"),
            auto_commit=False, ensure_exists=False, return_balance=False,
        )
    assert out is None
    goc.assert_not_awaited()                 # no ensure-exists read
    mock_db.execute.assert_awaited_once()    # only the UPDATE, no re-SELECT
    mock_db.commit.assert_not_awaited()      # auto_commit=False
    cdel.assert_awaited_once()               # cache still invalidated


async def test_default_path_ensures_and_returns(mock_db):
    balance = MagicMock()
    mock_db.execute.side_effect = [MagicMock(), _select_result(balance)]
    with patch("src.crud.credits.get_or_create_balance", new_callable=AsyncMock) as goc, \
         patch("src.crud.credits.cache_service.delete", new_callable=AsyncMock):
        out = await credits_crud.update_balance(
            mock_db, user_id=1, paid_posted_delta=Decimal("10"),
        )
    assert out is balance
    goc.assert_awaited_once()                # ensure-exists ran
    assert mock_db.execute.await_count == 2  # UPDATE + re-SELECT
    mock_db.commit.assert_awaited_once()     # auto_commit default True


async def test_return_balance_false_skips_reselect_even_when_ensuring(mock_db):
    with patch("src.crud.credits.get_or_create_balance", new_callable=AsyncMock) as goc, \
         patch("src.crud.credits.cache_service.delete", new_callable=AsyncMock):
        out = await credits_crud.update_balance(
            mock_db, user_id=1, paid_posted_delta=Decimal("3"),
            auto_commit=False, return_balance=False,
        )
    assert out is None
    goc.assert_awaited_once()                # ensure_exists default True -> ran
    mock_db.execute.assert_awaited_once()    # UPDATE only, no re-SELECT


async def test_update_uses_atomic_server_side_delta(mock_db):
    with patch("src.crud.credits.get_or_create_balance", new_callable=AsyncMock), \
         patch("src.crud.credits.cache_service.delete", new_callable=AsyncMock):
        await credits_crud.update_balance(
            mock_db, user_id=7, paid_holds_delta=Decimal("-5"),
            auto_commit=False, ensure_exists=False, return_balance=False,
        )
    stmt = mock_db.execute.await_args_list[0].args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    assert sql.lstrip().startswith("update")
    assert "coalesce(" in sql                # server-side, not Python read-modify-write
    assert "paid_pending_holds" in sql
