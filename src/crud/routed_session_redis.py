"""
Redis-backed routed session storage.

Uses Redis as the **sole** storage for routed session data — no database
involved.  Sessions are stored as Redis hashes with secondary set indexes
for efficient model-based and state-based lookups.

Data layout
-----------
- ``rsession:{session_id}``        – Hash with all session fields
- ``rsession:idx:model:{model_id}`` – Set of OPEN session IDs for a model
- ``rsession:idx:open``             – Set of all OPEN session IDs

Every session hash gets a TTL slightly beyond its ``expires_at`` so that
Redis automatically reclaims memory for long-expired sessions.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional, List

import redis.asyncio as aioredis
from redis.asyncio import ConnectionPool

from .routed_session_base import RoutedSessionStore, SessionData, SessionState
from ..core.config import settings
from ..core.logging_config import get_api_logger

logger = get_api_logger()

# Key prefixes / index keys
_SESSION_PREFIX = "rsession:"
_MODEL_IDX_PREFIX = "rsession:idx:model:"
_OPEN_IDX_KEY = "rsession:idx:open"

# Extra TTL (seconds) added beyond expires_at so the hash isn't evicted
# before the automation cleanup loop has a chance to close the session
# on the proxy router.  Only needs to survive a few cleanup cycles.
_TTL_BUFFER_SECONDS = 600


# ---------------------------------------------------------------------------
# Lua script for atomic "decrement but never below 0"
# ---------------------------------------------------------------------------
_RELEASE_LUA = """
local current = tonumber(redis.call('hget', KEYS[1], 'active_requests') or '0')
if current > 0 then
    redis.call('hset', KEYS[1], 'active_requests', tostring(current - 1))
    redis.call('hset', KEYS[1], 'updated_at', ARGV[1])
end
return current
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize(session: SessionData) -> dict:
    """Convert a SessionData to a flat dict suitable for ``HSET``."""
    return {
        "id": session.id,
        "model_id": session.model_id,
        "model_name": session.model_name or "",
        "state": session.state,
        "active_requests": str(session.active_requests),
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "last_used_at": session.last_used_at.isoformat() if session.last_used_at else "",
        "expires_at": session.expires_at.isoformat() if session.expires_at else "",
        "endpoint": session.endpoint or "",
        "error_reason": session.error_reason or "",
    }


def _deserialize(data: dict) -> SessionData:
    """Reconstruct a SessionData from a Redis hash dict."""

    def _parse_dt(value: str) -> Optional[datetime]:
        if not value:
            return None
        return datetime.fromisoformat(value)

    return SessionData(
        id=data["id"],
        model_id=data["model_id"],
        model_name=data.get("model_name") or None,
        state=data["state"],
        active_requests=int(data.get("active_requests", "0")),
        created_at=(
            _parse_dt(data.get("created_at", ""))
            or datetime.now(timezone.utc).replace(tzinfo=None)
        ),
        updated_at=(
            _parse_dt(data.get("updated_at", ""))
            or datetime.now(timezone.utc).replace(tzinfo=None)
        ),
        last_used_at=_parse_dt(data.get("last_used_at", "")),
        expires_at=_parse_dt(data.get("expires_at", "")),
        endpoint=data.get("endpoint") or None,
        error_reason=data.get("error_reason") or None,
    )


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------


class RedisRoutedSessionStore(RoutedSessionStore):
    """Redis-only session store."""

    def __init__(self) -> None:
        self._pool: Optional[ConnectionPool] = None
        self._redis: Optional[aioredis.Redis] = None
        self._initialized = False
        self._init_lock = asyncio.Lock()

    # -- connection management -----------------------------------------------

    async def _ensure_initialized(self) -> aioredis.Redis:
        if self._initialized and self._redis:
            return self._redis

        async with self._init_lock:
            if self._initialized and self._redis:
                return self._redis

            self._pool = ConnectionPool.from_url(
                settings.REDIS_URL,
                max_connections=settings.REDIS_MAX_CONNECTIONS,
                socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
                decode_responses=True,
            )
            self._redis = aioredis.Redis(connection_pool=self._pool)
            await self._redis.ping()
            self._initialized = True
            logger.info(
                "RedisRoutedSessionStore initialized",
                event_type="redis_session_store_init",
            )
            return self._redis

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
        if self._pool:
            await self._pool.disconnect()
        self._initialized = False

    # -- key helpers ----------------------------------------------------------

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"{_SESSION_PREFIX}{session_id}"

    @staticmethod
    def _model_index_key(model_id: str) -> str:
        return f"{_MODEL_IDX_PREFIX}{model_id}"

    # -- CRUD -----------------------------------------------------------------

    async def create(self, session: SessionData) -> SessionData:
        r = await self._ensure_initialized()
        key = self._session_key(session.id)

        pipe = r.pipeline()
        pipe.hset(key, mapping=_serialize(session))

        # Set a generous TTL so Redis reclaims the hash eventually
        if session.expires_at:
            ttl = int(
                (session.expires_at - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
            ) + _TTL_BUFFER_SECONDS
            if ttl > 0:
                pipe.expire(key, ttl)

        # Maintain indexes
        if session.state == SessionState.OPEN:
            pipe.sadd(_OPEN_IDX_KEY, session.id)
            pipe.sadd(self._model_index_key(session.model_id), session.id)

        await pipe.execute()
        logger.debug(
            "Session created in Redis",
            session_id=session.id,
            event_type="redis_session_created",
        )
        return session

    async def get(self, session_id: str) -> Optional[SessionData]:
        r = await self._ensure_initialized()
        data = await r.hgetall(self._session_key(session_id))
        if not data:
            return None
        return _deserialize(data)

    async def get_open_for_model(self, model_id: str) -> List[SessionData]:
        r = await self._ensure_initialized()
        session_ids = await r.smembers(self._model_index_key(model_id))
        if not session_ids:
            return []

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        sessions: List[SessionData] = []
        stale_ids: List[str] = []

        # Fan out HGETALL calls via pipeline for speed
        pipe = r.pipeline()
        ordered_ids = list(session_ids)
        for sid in ordered_ids:
            pipe.hgetall(self._session_key(sid))
        results = await pipe.execute()

        for sid, data in zip(ordered_ids, results):
            if not data:
                stale_ids.append(sid)
                continue
            s = _deserialize(data)
            if s.state != SessionState.OPEN:
                stale_ids.append(sid)
                continue
            if s.expires_at and s.expires_at <= now:
                continue  # expired — will be cleaned up by automation
            sessions.append(s)

        # Housekeeping: remove stale index entries
        if stale_ids:
            await r.srem(self._model_index_key(model_id), *stale_ids)

        # Sort by last_used_at ASC, nulls first
        sessions.sort(key=lambda s: s.last_used_at or datetime.min)
        return sessions

    async def get_all_open(self) -> List[SessionData]:
        r = await self._ensure_initialized()
        session_ids = await r.smembers(_OPEN_IDX_KEY)
        if not session_ids:
            return []

        sessions: List[SessionData] = []
        stale_ids: List[str] = []

        pipe = r.pipeline()
        ordered_ids = list(session_ids)
        for sid in ordered_ids:
            pipe.hgetall(self._session_key(sid))
        results = await pipe.execute()

        for sid, data in zip(ordered_ids, results):
            if not data:
                stale_ids.append(sid)
                continue
            s = _deserialize(data)
            if s.state != SessionState.OPEN:
                stale_ids.append(sid)
                continue
            sessions.append(s)

        if stale_ids:
            await r.srem(_OPEN_IDX_KEY, *stale_ids)

        return sessions

    async def get_expired_open(self) -> List[SessionData]:
        r = await self._ensure_initialized()
        session_ids = await r.smembers(_OPEN_IDX_KEY)
        if not session_ids:
            return []

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expired: List[SessionData] = []

        pipe = r.pipeline()
        ordered_ids = list(session_ids)
        for sid in ordered_ids:
            pipe.hgetall(self._session_key(sid))
        results = await pipe.execute()

        for sid, data in zip(ordered_ids, results):
            if not data:
                continue
            s = _deserialize(data)
            if s.state == SessionState.OPEN and s.expires_at and s.expires_at < now:
                expired.append(s)

        return expired

    async def assign_request(self, session_id: str) -> str:
        r = await self._ensure_initialized()
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        key = self._session_key(session_id)

        pipe = r.pipeline()
        pipe.hincrby(key, "active_requests", 1)
        pipe.hset(key, mapping={"last_used_at": now, "updated_at": now})
        await pipe.execute()
        return session_id

    async def release_request(self, session_id: str) -> None:
        r = await self._ensure_initialized()
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        key = self._session_key(session_id)
        await r.eval(_RELEASE_LUA, 1, key, now)

    async def update_state(
        self,
        session_id: str,
        state: str,
        error_reason: Optional[str] = None,
    ) -> None:
        r = await self._ensure_initialized()
        key = self._session_key(session_id)
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        # Read current data to maintain indexes
        data = await r.hgetall(key)
        if not data:
            return

        old_state = data.get("state")
        model_id = data.get("model_id")

        values: dict = {"state": state, "updated_at": now}
        if error_reason is not None:
            values["error_reason"] = error_reason

        pipe = r.pipeline()
        pipe.hset(key, mapping=values)

        # Update secondary indexes
        if old_state == SessionState.OPEN and state != SessionState.OPEN:
            pipe.srem(_OPEN_IDX_KEY, session_id)
            if model_id:
                pipe.srem(self._model_index_key(model_id), session_id)
        elif old_state != SessionState.OPEN and state == SessionState.OPEN:
            pipe.sadd(_OPEN_IDX_KEY, session_id)
            if model_id:
                pipe.sadd(self._model_index_key(model_id), session_id)

        await pipe.execute()
