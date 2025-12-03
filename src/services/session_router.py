"""
Session Router Service

Implements the session management decision tree and routing logic for
abstracted concurrency via the API Gateway.

This service manages:
1. Automatic session creation based on demand
2. Session reuse and routing
3. Session cleanup and optimization
4. Preferred model capacity management
"""

import asyncio
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from datetime import datetime, timedelta, timezone

from ..db.models import Session, UserAutomationSettings, APIKey
from ..crud import session as session_crud
from ..crud import automation as automation_crud
from ..services import session_service
from ..core.logging_config import get_api_logger
from ..core.config import settings

logger = get_api_logger()


class SessionRouter:
    """
    Manages session routing and capacity for the API Gateway.

    Implements the decision tree for:
    - Path A: Authorized request (demand-driven)
    - Path B: Automated activity (capacity management)
    """

    def __init__(self):
        self.session_locks = {}  # In-memory locks for session operations

    async def route_request_to_session(
        self,
        db: AsyncSession,
        api_key_id: int,
        user_id: int,
        model: str,
        session_duration: Optional[int] = None,
        model_type: Optional[str] = "LLM"
    ) -> Optional[Session]:
        """
        Path A: Authorized Request (Demand-Driven)

        Routes an incoming user request to an available session, creating
        new sessions as needed.

        Decision tree:
        1. Are there any open sessions? NO -> Open a session
        2. Are there any open sessions? YES -> Are all utilized?
           - YES → Open another session
           - NO → Route to open session

        Args:
            db: Database session
            api_key_id: API key ID making the request
            user_id: User ID making the request
            model: Model name or blockchain ID
            session_duration: Optional session duration override
            model_type: Type of model (LLM, embedding, etc.)

        Returns:
            Session object to use for the request
        """
        route_logger = logger.bind(
            user_id=user_id,
            api_key_id=api_key_id,
            model=model,
            event_type="session_routing"
        )

        route_logger.info("Routing request to session",
                         model=model,
                         event_type="route_request_start")

        # Get all open sessions for this user and model
        open_sessions = await self._get_open_sessions(db, user_id, model)

        # Are there any open sessions?
        if not open_sessions:
            route_logger.info("No open sessions found, creating new session",
                            model=model,
                            event_type="no_open_sessions")
            return await self._create_session_for_request(
                db, api_key_id, user_id, model, session_duration, model_type
            )

        # Are all sessions utilized?
        idle_session = await self._find_idle_session(db, open_sessions)

        if idle_session:
            route_logger.info("Routing to existing idle session",
                            session_id=idle_session.id,
                            event_type="route_to_idle_session")
            return idle_session

        # All sessions are busy - create another one
        route_logger.info("All sessions utilized, creating additional session",
                        active_sessions=len(open_sessions),
                        event_type="all_sessions_busy")
        return await self._create_session_for_request(
            db, api_key_id, user_id, model, session_duration, model_type
        )

    async def manage_automated_capacity(
        self,
        db: AsyncSession,
        user_id: int
    ) -> Dict[str, Any]:
        """
        Path B: Automated Activity (Capacity Management)

        Background process that manages session capacity based on:
        - Preferred models (maintain warm sessions)
        - Non-preferred models (aggressive cleanup)

        This should be called periodically (e.g., every 60 seconds) to
        optimize session utilization and costs.

        Args:
            db: Database session
            user_id: User ID to manage capacity for

        Returns:
            Dict with capacity management statistics
        """
        capacity_logger = logger.bind(
            user_id=user_id,
            event_type="capacity_management"
        )

        capacity_logger.info("Starting automated capacity management",
                           user_id=user_id,
                           event_type="capacity_management_start")

        # Get automation settings
        automation_settings = await automation_crud.get_automation_settings(db, user_id)

        if not automation_settings or not automation_settings.is_enabled:
            capacity_logger.info("Automation disabled for user",
                               user_id=user_id,
                               event_type="automation_disabled")
            return {"managed": False, "reason": "automation_disabled"}

        preferred_models = automation_settings.preferred_models or []

        stats = {
            "preferred_models_managed": 0,
            "non_preferred_models_managed": 0,
            "sessions_created": 0,
            "sessions_closed": 0,
            "total_sessions": 0
        }

        # Get all active models for this user
        active_models = await self._get_active_models_for_user(db, user_id)

        for model in active_models:
            is_preferred = model in preferred_models

            if is_preferred:
                result = await self._manage_preferred_model(
                    db, user_id, model, automation_settings
                )
                stats["preferred_models_managed"] += 1
            else:
                result = await self._manage_non_preferred_model(
                    db, user_id, model
                )
                stats["non_preferred_models_managed"] += 1

            stats["sessions_created"] += result.get("sessions_created", 0)
            stats["sessions_closed"] += result.get("sessions_closed", 0)

        stats["total_sessions"] = await self._count_user_sessions(db, user_id)

        capacity_logger.info("Completed automated capacity management",
                           stats=stats,
                           event_type="capacity_management_complete")

        return stats

    async def _manage_preferred_model(
        self,
        db: AsyncSession,
        user_id: int,
        model: str,
        automation_settings: UserAutomationSettings
    ) -> Dict[str, int]:
        """
        Manage capacity for a preferred model.

        Decision tree:
        1. Are there any open sessions? NO → Open a session
        2. Are there any open sessions? YES → Are all utilized?
           - YES → Open a session (scale up)
           - NO → How many are unutilized?
             * More than 1 → Close a session (reduce capacity)
             * Exactly 1 → Do nothing (keep buffer)
        """
        pref_logger = logger.bind(
            user_id=user_id,
            model=model,
            event_type="preferred_model_management"
        )

        open_sessions = await self._get_open_sessions(db, user_id, model)

        stats = {"sessions_created": 0, "sessions_closed": 0}

        # Are there any open sessions?
        if not open_sessions:
            pref_logger.info("No sessions for preferred model, creating one",
                           model=model,
                           event_type="preferred_model_no_sessions")
            # Create a session (we'll need an API key - get the default one)
            api_key = await self._get_default_api_key(db, user_id)
            if api_key:
                await self._create_session_for_automation(
                    db, api_key.id, user_id, model, automation_settings.session_duration
                )
                stats["sessions_created"] = 1
            return stats

        # Are all sessions utilized?
        idle_sessions = await self._find_all_idle_sessions(db, open_sessions)

        if not idle_sessions:
            # All sessions busy - scale up
            pref_logger.info("All sessions utilized for preferred model, scaling up",
                           model=model,
                           current_sessions=len(open_sessions),
                           event_type="preferred_model_scale_up")

            # Check max sessions limit
            max_sessions = automation_settings.max_sessions_per_model or 5
            if len(open_sessions) < max_sessions:
                api_key = await self._get_default_api_key(db, user_id)
                if api_key:
                    await self._create_session_for_automation(
                        db, api_key.id, user_id, model, automation_settings.session_duration
                    )
                    stats["sessions_created"] = 1
            else:
                pref_logger.warning("Max sessions reached for preferred model",
                                  model=model,
                                  max_sessions=max_sessions,
                                  event_type="preferred_model_max_sessions")
        else:
            # Some sessions idle - check if we should trim
            min_idle = automation_settings.min_idle_sessions or 1

            if len(idle_sessions) > min_idle:
                # Too many idle sessions - close extras
                sessions_to_close = len(idle_sessions) - min_idle
                pref_logger.info("Closing excess idle sessions for preferred model",
                               model=model,
                               idle_sessions=len(idle_sessions),
                               to_close=sessions_to_close,
                               event_type="preferred_model_trim_capacity")

                for session in idle_sessions[:sessions_to_close]:
                    await session_service.close_session(db, session.id)
                    stats["sessions_closed"] += 1
            else:
                pref_logger.debug("Optimal idle session count for preferred model",
                                model=model,
                                idle_sessions=len(idle_sessions),
                                event_type="preferred_model_optimal")

        return stats

    async def _manage_non_preferred_model(
        self,
        db: AsyncSession,
        user_id: int,
        model: str
    ) -> Dict[str, int]:
        """
        Manage capacity for a non-preferred model.

        Decision tree:
        1. Are there any open sessions? NO → Do nothing
        2. Are there any open sessions? YES → Are all utilized?
           - YES → May route to preferred logic if needed
           - NO → Close a session (aggressive cleanup)
        """
        non_pref_logger = logger.bind(
            user_id=user_id,
            model=model,
            event_type="non_preferred_model_management"
        )

        open_sessions = await self._get_open_sessions(db, user_id, model)

        stats = {"sessions_created": 0, "sessions_closed": 0}

        # Are there any open sessions?
        if not open_sessions:
            # Don't pre-create sessions for non-preferred models
            non_pref_logger.debug("No sessions for non-preferred model, not creating",
                                 model=model,
                                 event_type="non_preferred_model_no_action")
            return stats

        # DAre all sessions utilized?
        idle_sessions = await self._find_all_idle_sessions(db, open_sessions)

        if idle_sessions:
            # Close all idle sessions for non-preferred models (aggressive cleanup)
            non_pref_logger.info("Closing idle sessions for non-preferred model",
                               model=model,
                               idle_sessions=len(idle_sessions),
                               event_type="non_preferred_model_cleanup")

            for session in idle_sessions:
                await session_service.close_session(db, session.id)
                stats["sessions_closed"] += 1
        else:
            non_pref_logger.debug("All sessions utilized for non-preferred model",
                                model=model,
                                event_type="non_preferred_model_all_busy")

        return stats

    async def _get_open_sessions(
        self,
        db: AsyncSession,
        user_id: int,
        model: str
    ) -> List[Session]:
        """Get all open (active, non-expired) sessions for a user and model."""
        result = await db.execute(
            select(Session)
            .where(
                and_(
                    Session.user_id == user_id,
                    Session.model == model,
                    Session.is_active == True,
                    Session.expires_at > datetime.now()
                )
            )
        )
        return result.scalars().all()

    async def _find_idle_session(
        self,
        db: AsyncSession,
        sessions: List[Session]
    ) -> Optional[Session]:
        """
        Find an idle session from a list of sessions.

        A session is idle if:
        - utilization_status is 'idle'
        - OR last_request_at is more than 5 seconds ago
        - OR last_request_at is None
        """
        for session in sessions:
            # Refresh session data to get latest status
            await db.refresh(session)

            if session.utilization_status == "idle":
                return session

            if session.last_request_at is None:
                return session

            time_since_last = datetime.now() - session.last_request_at
            if time_since_last.total_seconds() > 5:
                return session

        return None

    async def _find_all_idle_sessions(
        self,
        db: AsyncSession,
        sessions: List[Session]
    ) -> List[Session]:
        """Find all idle sessions from a list."""
        idle_sessions = []

        for session in sessions:
            await db.refresh(session)

            is_idle = (
                session.utilization_status == "idle" or
                session.last_request_at is None or
                (datetime.now() - session.last_request_at).total_seconds() > 5
            )

            if is_idle:
                idle_sessions.append(session)

        return idle_sessions

    async def _create_session_for_request(
        self,
        db: AsyncSession,
        api_key_id: int,
        user_id: int,
        model: str,
        session_duration: Optional[int],
        model_type: str
    ) -> Optional[Session]:
        """Create a session for an incoming request."""
        return await session_service.create_automated_session(
            db=db,
            api_key_id=api_key_id,
            user_id=user_id,
            requested_model=model,
            session_duration=session_duration,
            model_type=model_type
        )

    async def _create_session_for_automation(
        self,
        db: AsyncSession,
        api_key_id: int,
        user_id: int,
        model: str,
        session_duration: int
    ) -> Optional[Session]:
        """Create a session for automated capacity management."""
        return await session_service.create_automated_session(
            db=db,
            api_key_id=api_key_id,
            user_id=user_id,
            requested_model=model,
            session_duration=session_duration,
            model_type="LLM"
        )

    async def _get_active_models_for_user(
        self,
        db: AsyncSession,
        user_id: int
    ) -> List[str]:
        """Get list of models that have active sessions for this user."""
        result = await db.execute(
            select(Session.model)
            .where(
                and_(
                    Session.user_id == user_id,
                    Session.is_active == True,
                    Session.expires_at > datetime.now()
                )
            )
            .distinct()
        )
        return [row[0] for row in result.all()]

    async def _get_default_api_key(
        self,
        db: AsyncSession,
        user_id: int
    ) -> Optional[APIKey]:
        """Get the default API key for a user."""
        result = await db.execute(
            select(APIKey)
            .where(
                and_(
                    APIKey.user_id == user_id,
                    APIKey.is_active == True,
                    APIKey.is_default == True
                )
            )
        )
        return result.scalar_one_or_none()

    async def _count_user_sessions(
        self,
        db: AsyncSession,
        user_id: int
    ) -> int:
        """Count total active sessions for a user."""
        result = await db.execute(
            select(func.count(Session.id))
            .where(
                and_(
                    Session.user_id == user_id,
                    Session.is_active == True,
                    Session.expires_at > datetime.now()
                )
            )
        )
        return result.scalar()

    async def mark_session_busy(
        self,
        db: AsyncSession,
        session_id: str
    ):
        """Mark a session as busy (processing a request)."""
        session = await session_crud.get_session_by_id(db, session_id)
        if session:
            session.utilization_status = "busy"
            session.last_request_at = datetime.now()
            await db.commit()

    async def mark_session_idle(
        self,
        db: AsyncSession,
        session_id: str
    ):
        """Mark a session as idle (available for requests)."""
        session = await session_crud.get_session_by_id(db, session_id)
        if session:
            session.utilization_status = "idle"
            session.request_count = (session.request_count or 0) + 1
            await db.commit()

    async def get_session_utilization_stats(
        self,
        db: AsyncSession,
        user_id: int
    ) -> Dict[str, Any]:
        """
        Get utilization statistics for a user's sessions.

        Returns:
            Dict with utilization metrics
        """
        result = await db.execute(
            select(
                Session.model,
                Session.utilization_status,
                func.count(Session.id).label("count")
            )
            .where(
                and_(
                    Session.user_id == user_id,
                    Session.is_active == True,
                    Session.expires_at > datetime.now()
                )
            )
            .group_by(Session.model, Session.utilization_status)
        )

        stats_by_model = {}

        for row in result:
            model = row.model
            status = row.utilization_status
            count = row.count

            if model not in stats_by_model:
                stats_by_model[model] = {
                    "idle": 0,
                    "busy": 0,
                    "total": 0
                }

            stats_by_model[model][status] = count
            stats_by_model[model]["total"] += count

        # Calculate overall utilization
        total_sessions = sum(m["total"] for m in stats_by_model.values())
        total_busy = sum(m["busy"] for m in stats_by_model.values())

        return {
            "by_model": stats_by_model,
            "overall": {
                "total_sessions": total_sessions,
                "busy_sessions": total_busy,
                "idle_sessions": total_sessions - total_busy,
                "utilization_rate": (total_busy / total_sessions * 100) if total_sessions > 0 else 0
            }
        }


# Global session router instance
session_router = SessionRouter()
