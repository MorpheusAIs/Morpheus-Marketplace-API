"""
Database-backed routed session storage.

Uses SQLAlchemy with the RoutedSession ORM model for persistent session
storage.  No caching layer — every operation hits the database directly.
"""

from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import select, update

from .routed_session_base import RoutedSessionStore, SessionData, SessionState
from ..db.models import RoutedSession
from ..db.models.routed_session import SessionState as DBSessionState
from ..db.database import get_db
from ..core.logging_config import get_api_logger

logger = get_api_logger()


def _model_to_data(model: RoutedSession) -> SessionData:
    """Convert an ORM RoutedSession to a plain SessionData."""
    return SessionData(
        id=model.id,
        model_id=model.model_id,
        model_name=model.model_name,
        state=model.state if isinstance(model.state, str) else model.state.value,
        active_requests=model.active_requests,
        created_at=model.created_at,
        updated_at=model.updated_at,
        last_used_at=model.last_used_at,
        expires_at=model.expires_at,
        endpoint=model.endpoint,
        error_reason=model.error_reason,
    )


class DBRoutedSessionStore(RoutedSessionStore):
    """PostgreSQL / SQLAlchemy-backed session store (no caching)."""

    async def create(self, session: SessionData) -> SessionData:
        async with get_db() as db:
            model = RoutedSession(
                id=session.id,
                model_id=session.model_id,
                model_name=session.model_name,
                state=session.state,
                active_requests=session.active_requests,
                created_at=session.created_at,
                updated_at=session.updated_at,
                last_used_at=session.last_used_at,
                expires_at=session.expires_at,
                endpoint=session.endpoint,
                error_reason=session.error_reason,
            )
            db.add(model)
            await db.commit()
            await db.refresh(model)
            logger.debug(
                "Session created in DB",
                session_id=session.id,
                event_type="db_session_created",
            )
            return _model_to_data(model)

    async def get(self, session_id: str) -> Optional[SessionData]:
        async with get_db() as db:
            result = await db.execute(
                select(RoutedSession).where(RoutedSession.id == session_id)
            )
            model = result.scalar_one_or_none()
            return _model_to_data(model) if model else None

    async def get_open_for_model(self, model_id: str) -> List[SessionData]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with get_db() as db:
            result = await db.execute(
                select(RoutedSession)
                .where(
                    RoutedSession.model_id == model_id,
                    RoutedSession.state == DBSessionState.OPEN,
                    RoutedSession.expires_at > now,
                )
                .order_by(RoutedSession.last_used_at.asc().nullsfirst())
            )
            return [_model_to_data(m) for m in result.scalars().all()]

    async def get_all_open(self) -> List[SessionData]:
        async with get_db() as db:
            result = await db.execute(
                select(RoutedSession).where(
                    RoutedSession.state == DBSessionState.OPEN,
                )
            )
            return [_model_to_data(m) for m in result.scalars().all()]

    async def get_expired_open(self) -> List[SessionData]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with get_db() as db:
            result = await db.execute(
                select(RoutedSession).where(
                    RoutedSession.state == DBSessionState.OPEN,
                    RoutedSession.expires_at < now,
                )
            )
            return [_model_to_data(m) for m in result.scalars().all()]

    async def assign_request(self, session_id: str) -> str:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with get_db() as db:
            await db.execute(
                update(RoutedSession)
                .where(RoutedSession.id == session_id)
                .values(
                    active_requests=RoutedSession.active_requests + 1,
                    last_used_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
        return session_id

    async def release_request(self, session_id: str) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with get_db() as db:
            await db.execute(
                update(RoutedSession)
                .where(
                    RoutedSession.id == session_id,
                    RoutedSession.active_requests > 0,
                )
                .values(
                    active_requests=RoutedSession.active_requests - 1,
                    updated_at=now,
                )
            )
            await db.commit()

    async def update_state(
        self,
        session_id: str,
        state: str,
        error_reason: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        values: dict = {"state": state, "updated_at": now}
        if error_reason is not None:
            values["error_reason"] = error_reason
        async with get_db() as db:
            await db.execute(
                update(RoutedSession)
                .where(RoutedSession.id == session_id)
                .values(**values)
            )
            await db.commit()
