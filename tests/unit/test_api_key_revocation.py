"""Regression tests for C4: revoked API keys (and inactive users) must NOT
authenticate, on either the cache-hit or DB-fallback path of get_api_key_auth.

Before the fix, neither `_build_auth_from_cache` nor `_build_auth_from_db`
checked `is_active`, so a soft-deleted key kept working (and got re-cached).
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.db.models import User, APIKey
from src.dependencies import _build_auth_from_cache, _build_auth_from_db, APIKeyAuth

PREFIX = "sk-abc123"
RAW = "sk-abc123456789"


def _cached(*, key_active=True, user_active=True):
    return {
        "id": 1,
        "user_id": 7,
        "key_prefix": PREFIX,
        "hashed_key": "hashed",
        "encrypted_key": "enc",  # not None => hash-verified path
        "is_active": key_active,
        "last_used_at": None,
        "created_at": None,
        "name": "k",
        "encryption_version": 1,
        "is_default": False,
        "user": {
            "id": 7,
            "is_active": user_active,
            "cognito_user_id": "cog",
            "created_at": None,
            "updated_at": None,
            "rate_limit_multiplier": 1.0,
        },
    }


# --------------------------------------------------------------------------- #
# Cache-hit path
# --------------------------------------------------------------------------- #


async def test_cache_revoked_key_rejected_and_evicted():
    delete = AsyncMock()
    with patch("src.dependencies.verify_api_key", return_value=True), \
         patch("src.dependencies.cache_service.delete", delete):
        with pytest.raises(HTTPException) as exc:
            await _build_auth_from_cache(_cached(key_active=False), RAW, PREFIX)
    assert exc.value.status_code == 401
    delete.assert_awaited_once_with("api_key", PREFIX)


async def test_cache_inactive_user_rejected_and_evicted():
    delete = AsyncMock()
    with patch("src.dependencies.verify_api_key", return_value=True), \
         patch("src.dependencies.cache_service.delete", delete):
        with pytest.raises(HTTPException) as exc:
            await _build_auth_from_cache(_cached(user_active=False), RAW, PREFIX)
    assert exc.value.status_code == 401
    delete.assert_awaited_once_with("api_key", PREFIX)


async def test_cache_active_key_authenticates():
    with patch("src.dependencies.verify_api_key", return_value=True), \
         patch("src.dependencies._update_api_key_last_used_background", new_callable=AsyncMock):
        auth = await _build_auth_from_cache(_cached(), RAW, PREFIX)
    assert isinstance(auth, APIKeyAuth)
    assert auth.user.id == 7
    assert auth.api_key.is_active is True


# --------------------------------------------------------------------------- #
# DB-fallback path
# --------------------------------------------------------------------------- #


class _FakeGetDb:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


def _db_with_key(api_key):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = api_key
    db.execute = AsyncMock(return_value=result)
    return db


def _key(*, key_active=True, user_active=True):
    user = User(id=7, is_active=user_active, cognito_user_id="cog", rate_limit_multiplier=1.0)
    key = APIKey(
        id=1, user_id=7, key_prefix=PREFIX, hashed_key="hashed", encrypted_key="enc",
        is_active=key_active, name="k", encryption_version=1, is_default=False,
    )
    key.user = user
    return key


async def test_db_revoked_key_rejected_and_not_recached():
    db = _db_with_key(_key(key_active=False))
    cache_set = AsyncMock()
    with patch("src.dependencies.get_db", lambda: _FakeGetDb(db)), \
         patch("src.dependencies.verify_api_key", return_value=True), \
         patch("src.dependencies.api_key_crud.update_last_used", new_callable=AsyncMock) as upd, \
         patch("src.dependencies.cache_service.set", cache_set):
        with pytest.raises(HTTPException) as exc:
            await _build_auth_from_db(RAW, PREFIX)
    assert exc.value.status_code == 401
    cache_set.assert_not_awaited()   # revoked key never re-cached
    upd.assert_not_awaited()         # nor does it bump last_used


async def test_db_inactive_user_rejected():
    db = _db_with_key(_key(user_active=False))
    cache_set = AsyncMock()
    with patch("src.dependencies.get_db", lambda: _FakeGetDb(db)), \
         patch("src.dependencies.verify_api_key", return_value=True), \
         patch("src.dependencies.api_key_crud.update_last_used", new_callable=AsyncMock), \
         patch("src.dependencies.cache_service.set", cache_set):
        with pytest.raises(HTTPException) as exc:
            await _build_auth_from_db(RAW, PREFIX)
    assert exc.value.status_code == 401
    cache_set.assert_not_awaited()


async def test_db_active_key_authenticates_and_caches():
    db = _db_with_key(_key())
    cache_set = AsyncMock()
    with patch("src.dependencies.get_db", lambda: _FakeGetDb(db)), \
         patch("src.dependencies.verify_api_key", return_value=True), \
         patch("src.dependencies.api_key_crud.update_last_used", new_callable=AsyncMock), \
         patch("src.dependencies.cache_service.set", cache_set):
        auth = await _build_auth_from_db(RAW, PREFIX)
    assert isinstance(auth, APIKeyAuth)
    assert auth.user.id == 7
    cache_set.assert_awaited_once()  # active key is cached
