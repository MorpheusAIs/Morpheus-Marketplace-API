"""
Abstract base for routed session storage.

Defines the SessionData dataclass and the RoutedSessionStore interface
that both DB and Redis implementations must follow.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List


class SessionState:
    """Session lifecycle states (plain string constants)."""
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


@dataclass
class SessionData:
    """
    Plain data object representing a routed session.

    Used by both DB and Redis storage implementations so the routing
    service never depends on ORM models directly.
    """
    id: str
    model_id: str
    model_name: Optional[str] = None
    state: str = SessionState.OPEN
    active_requests: int = 0
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    endpoint: Optional[str] = None
    error_reason: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return self.state == SessionState.OPEN

    @property
    def is_utilized(self) -> bool:
        return self.active_requests > 0

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return now > self.expires_at


class RoutedSessionStore(ABC):
    """
    Abstract interface for routed session CRUD operations.

    Implementations must be fully self-contained — they manage their own
    connections (DB pool / Redis pool) so callers never pass a connection handle.
    """

    @abstractmethod
    async def create(self, session: SessionData) -> SessionData:
        """Persist a new session record and return it."""
        ...

    @abstractmethod
    async def get(self, session_id: str) -> Optional[SessionData]:
        """Return a session by ID, or None."""
        ...

    @abstractmethod
    async def get_open_for_model(self, model_id: str) -> List[SessionData]:
        """
        Return all OPEN, non-expired sessions for *model_id*,
        ordered by last_used_at ASC (nulls first).
        """
        ...

    @abstractmethod
    async def get_all_open(self) -> List[SessionData]:
        """Return all sessions in OPEN state (regardless of expiry)."""
        ...

    @abstractmethod
    async def get_expired_open(self) -> List[SessionData]:
        """Return all OPEN sessions whose expires_at is in the past."""
        ...

    @abstractmethod
    async def assign_request(self, session_id: str) -> str:
        """
        Atomically increment active_requests and set last_used_at.
        Returns the session_id for convenience.
        """
        ...

    @abstractmethod
    async def release_request(self, session_id: str) -> None:
        """Atomically decrement active_requests (never below 0)."""
        ...

    @abstractmethod
    async def update_state(
        self,
        session_id: str,
        state: str,
        error_reason: Optional[str] = None,
    ) -> None:
        """Update session state (and optionally error_reason)."""
        ...


# ---------------------------------------------------------------------------
# Factory – returns the singleton store selected by SESSION_STORAGE_BACKEND
# ---------------------------------------------------------------------------

_store_instance: Optional[RoutedSessionStore] = None


def get_session_store() -> RoutedSessionStore:
    """
    Return the singleton RoutedSessionStore based on the
    SESSION_STORAGE_BACKEND setting ("db" or "redis").
    """
    global _store_instance
    if _store_instance is not None:
        return _store_instance

    from ..core.config import settings  # late import to avoid circular deps

    backend = getattr(settings, "SESSION_STORAGE_BACKEND", "db").lower()

    if backend == "redis":
        from .routed_session_redis import RedisRoutedSessionStore
        _store_instance = RedisRoutedSessionStore()
    else:
        from .routed_session import DBRoutedSessionStore
        _store_instance = DBRoutedSessionStore()

    return _store_instance
