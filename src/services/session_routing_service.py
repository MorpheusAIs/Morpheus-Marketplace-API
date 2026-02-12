"""
Session Routing Service

This service implements intelligent session routing with automatic scaling:
1. Routes authorized inference requests to open sessions
2. Opens new sessions when no sessions exist or all are utilized
3. Runs an automated activity loop for preferred model management

Key concepts:
- Model: identifier (e.g., "llama-3.3-70b" or modelID hex)
- Session: connection slot to a provider for a model
- Open session: session in state OPEN
- Utilized session: session with active_requests > 0
- Preferred model: configured list for automatic session pre-warming

Storage backend is selected via the SESSION_STORAGE_BACKEND env var
("db" or "redis") and accessed through the RoutedSessionStore abstraction.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

from ..crud.routed_session_base import (
    SessionData,
    SessionState,
    get_session_store,
)
from ..core.config import settings
from ..core.model_routing import model_router
from ..core.logging_config import get_api_logger
from ..services import proxy_router_service

logger = get_api_logger()


class SessionRoutingError(Exception):
    """Base exception for session routing errors."""
    
    def __init__(self, message: str, model_id: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.model_id = model_id


class NoSessionAvailableError(SessionRoutingError):
    """Raised when no session can be acquired for a request."""
    pass


class SessionOpenError(SessionRoutingError):
    """Raised when session opening fails."""
    pass


class SessionRoutingService:
    """
    Service for routing requests to sessions and managing session lifecycle.
    
    Thread-safety:
    - Uses per-model locking to prevent race conditions
    - Storage operations are atomic where needed
    """
    
    def __init__(self):
        # Per-model locks for request routing
        self._model_locks: Dict[str, asyncio.Lock] = {}
        self._locks_lock = asyncio.Lock()
        
        # Automation loop control
        self._automation_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        
        logger.info("SessionRoutingService initialized",
                   event_type="session_routing_service_init")
    
    @property
    def _store(self):
        """Lazy access to the session store singleton."""
        return get_session_store()

    async def _get_model_lock(self, model_id: str) -> asyncio.Lock:
        """Get or create a lock for a specific model."""
        async with self._locks_lock:
            if model_id not in self._model_locks:
                self._model_locks[model_id] = asyncio.Lock()
            return self._model_locks[model_id]
    
    def _get_preferred_models(self) -> set:
        """Get the set of preferred model IDs from configuration."""
        if not settings.SESSION_PREFERRED_MODELS:
            return set()
        return set(m.strip() for m in settings.SESSION_PREFERRED_MODELS.split(",") if m.strip())
    
    # =========================================================================
    # REQUEST PATH: Route requests to sessions
    # =========================================================================
    
    async def route_request(
        self,
        user_id: int,
        requested_model: Optional[str] = None,
        model_type: str = "LLM"
    ) -> str:
        """
        Route an authorized request to an appropriate session.
        
        This is the main entry point for the request path.
        Sessions are shared across users - user_id is only used for
        private key lookup when opening new sessions.
        
        Args:
            user_id: User ID for private key lookup when opening sessions
            requested_model: Model name or blockchain ID requested
            model_type: Type of model (LLM, EMBEDDINGS, TTS, STT)
            
        Returns:
            str: Session ID to use for the request
            
        Raises:
            NoSessionAvailableError: If no session could be acquired
            SessionOpenError: If session opening failed
        """
        route_logger = logger.bind(
            user_id=user_id,
            requested_model=requested_model,
            model_type=model_type
        )
        
        # Resolve model to blockchain ID
        model_id = await model_router.get_target_model(requested_model, type=model_type)
        route_logger = route_logger.bind(model_id=model_id)
        
        route_logger.info("Routing request to session",
                         event_type="route_request_start")
        
        # Acquire per-model lock to prevent race conditions
        model_lock = await self._get_model_lock(model_id)
        
        async with model_lock:
            # Step 1: Check if any open sessions exist for this model
            open_sessions = await self._store.get_open_for_model(model_id)
            
            if not open_sessions:
                # No open sessions - create one
                route_logger.info("No open sessions for model, creating new session",
                                 event_type="no_open_sessions")
                session = await self._open_session_for_model(
                    model_id, requested_model, model_type, user_id
                )
                return await self._store.assign_request(session.id)
            
            # Step 2: Check if all sessions are utilized
            unutilized_sessions = [s for s in open_sessions if not s.is_utilized]
            
            if unutilized_sessions:
                # Route to an unutilized session (least recently used)
                session = min(
                    unutilized_sessions,
                    key=lambda s: s.last_used_at or s.created_at
                )
                route_logger.info("Routing to unutilized session",
                                 session_id=session.id,
                                 event_type="route_to_unutilized")
                return await self._store.assign_request(session.id)
            
            # Step 3: All sessions utilized - open another
            route_logger.info("All sessions utilized, opening another",
                             current_count=len(open_sessions),
                             event_type="all_sessions_utilized")
            session = await self._open_session_for_model(
                model_id, requested_model, model_type, user_id
            )
            return await self._store.assign_request(session.id)
    
    async def release_session(self, session_id: str) -> None:
        """
        Release a session after request completion.
        
        Decrements active_requests counter atomically.
        
        Args:
            session_id: Session ID to release
        """
        release_logger = logger.bind(session_id=session_id)
        
        try:
            await self._store.release_request(session_id)
            
            release_logger.debug("Session released",
                               event_type="session_released")
            
        except Exception as e:
            release_logger.error("Error releasing session",
                               error=str(e),
                               event_type="session_release_error",
                               exc_info=True)
    
    @asynccontextmanager
    async def session_context(
        self,
        user_id: int,
        requested_model: Optional[str] = None,
        model_type: str = "LLM"
    ):
        """
        Context manager for session routing with automatic release.
        
        Usage:
            async with session_routing_service.session_context(user_id, model) as session_id:
                # Use session_id for request
                ...
            # Session automatically released on exit
        """
        session_id = await self.route_request(
            user_id, requested_model, model_type
        )
        
        try:
            yield session_id
        finally:
            await self.release_session(session_id)
    
    # =========================================================================
    # SESSION LIFECYCLE: Open and close sessions
    # =========================================================================
    
    async def _open_session_for_model(
        self,
        model_id: str,
        model_name: Optional[str] = None,
        model_type: str = "LLM",
        user_id: Optional[int] = None
    ) -> SessionData:
        """
        Open a new session for a model.
        
        Sessions are shared across users. The user_id is only used for
        private key lookup when calling the proxy router.
        
        Note: Record is only created after successful session opening (no OPENING state).
        
        Args:
            model_id: Blockchain model ID (hex string)
            model_name: Human-readable model name
            model_type: Type of model
            user_id: Optional user ID for private key lookup (uses fallback if not provided)
            
        Returns:
            SessionData: The newly opened session
            
        Raises:
            SessionOpenError: If session opening fails
        """
        open_logger = logger.bind(
            model_id=model_id,
            model_name=model_name
        )
        
        open_logger.info("Opening new session for model",
                        event_type="session_open_start")
        
        session_duration = settings.SESSION_DEFAULT_DURATION_SECONDS
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=session_duration)
        
        try:
            # Call proxy router to open session
            open_logger.info("Calling proxy router to open session",
                           user_id=user_id,
                           event_type="proxy_open_session_start")
            
            response = await proxy_router_service.openSession(
                target_model=model_id,
                session_duration=session_duration,
                failover=False,
                direct_payment=False
            )
            
            blockchain_session_id = response.get("sessionID")
            
            if not blockchain_session_id:
                raise SessionOpenError(
                    "No session ID returned from proxy router",
                    model_id=model_id
                )
            
            # Determine endpoint
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            endpoint = "/v1/chat/completions"
            if model_type == "EMBEDDINGS":
                endpoint = "/v1/embeddings"
            elif model_type == "AUTOMATION":
                endpoint = ""
            
            session_data = SessionData(
                id=blockchain_session_id,
                model_id=model_id,
                model_name=model_name,
                state=SessionState.OPEN,
                expires_at=expires_at,
                active_requests=0,
                created_at=now,
                updated_at=now,
                endpoint=endpoint,
            )
            
            session = await self._store.create(session_data)
            
            open_logger.info("Session opened successfully",
                           session_id=blockchain_session_id,
                           event_type="session_opened")
            
            return session
            
        except proxy_router_service.ProxyRouterServiceError as e:
            open_logger.error("Proxy router error opening session",
                            error=str(e),
                            event_type="session_open_proxy_error")
            
            raise SessionOpenError(
                f"Failed to open session: {e.message}",
                model_id=model_id
            ) from e
            
        except Exception as e:
            open_logger.error("Error opening session",
                            error=str(e),
                            event_type="session_open_error",
                            exc_info=True)
            
            raise SessionOpenError(
                f"Failed to open session: {str(e)}",
                model_id=model_id
            ) from e
    
    async def _close_session(self, session: SessionData) -> bool:
        """
        Close a session safely.
        
        Args:
            session: Session to close
            
        Returns:
            bool: True if closed successfully
        """
        close_logger = logger.bind(session_id=session.id, model_id=session.model_id)
        
        # Safety check: don't close utilized sessions
        if session.is_utilized:
            close_logger.warning("Cannot close utilized session",
                               active_requests=session.active_requests,
                               event_type="close_utilized_session_rejected")
            return False
        
        # Mark as CLOSING
        await self._store.update_state(session.id, SessionState.CLOSING)
        
        close_logger.info("Closing session",
                         event_type="session_close_start")
        
        try:
            # Call proxy router to close session
            await proxy_router_service.closeSession(session.id)
            
            # Mark as CLOSED
            await self._store.update_state(session.id, SessionState.CLOSED)
            
            close_logger.info("Session closed successfully",
                            event_type="session_closed")
            return True
            
        except proxy_router_service.ProxyRouterServiceError as e:
            await self._store.update_state(
                session.id, SessionState.FAILED,
                error_reason=f"Close failed: {str(e)}"
            )
            
            close_logger.error("Error closing session",
                             error=str(e),
                             event_type="session_close_error")
            return False
        
        except Exception as e:
            await self._store.update_state(
                session.id, SessionState.FAILED,
                error_reason=f"Close failed: {str(e)}"
            )
            
            close_logger.error("Unexpected error closing session",
                             error=str(e),
                             event_type="session_close_unexpected_error",
                             exc_info=True)
            return False
    
    # =========================================================================
    # AUTOMATED ACTIVITY LOOP
    # =========================================================================
    
    async def start_automation_loop(self) -> None:
        """Start the automated activity loop."""
        if self._automation_task is not None:
            logger.warning("Automation loop already running",
                          event_type="automation_loop_already_running")
            return
        
        self._shutdown_event.clear()
        self._automation_task = asyncio.create_task(self._automation_loop())
        
        logger.info("Automation loop started",
                   interval_seconds=settings.SESSION_AUTOMATION_INTERVAL_SECONDS,
                   event_type="automation_loop_started")
    
    async def stop_automation_loop(self) -> None:
        """Stop the automated activity loop."""
        if self._automation_task is None:
            return
        
        self._shutdown_event.set()
        self._automation_task.cancel()
        
        try:
            await self._automation_task
        except asyncio.CancelledError:
            pass
        
        self._automation_task = None
        logger.info("Automation loop stopped",
                   event_type="automation_loop_stopped")
    
    async def _automation_loop(self) -> None:
        """
        Automated activity loop for session management.
        
        Runs periodically and applies scaling logic based on:
        - Preferred models: Keep at least one idle session, scale up if all utilized
        - Non-preferred models: Close idle sessions to free resources
        """
        auto_logger = logger.bind(component="automation_loop")
        
        auto_logger.info("Automation loop starting",
                        event_type="automation_loop_start")
        
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(settings.SESSION_AUTOMATION_INTERVAL_SECONDS)
                
                if self._shutdown_event.is_set():
                    break
                
                auto_logger.debug("Running automation cycle",
                                event_type="automation_cycle_start")
                
                await self._run_automation_cycle()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                auto_logger.error("Error in automation cycle",
                                error=str(e),
                                event_type="automation_cycle_error",
                                exc_info=True)
    
    async def _run_automation_cycle(self) -> None:
        """
        Run one cycle of the automation loop.
        
        For each model with open sessions:
        - If preferred: ensure at least one idle, scale up if all utilized
        - If not preferred: close idle sessions
        """
        auto_logger = logger.bind(component="automation_cycle")
        
        preferred_models = self._get_preferred_models()
        sessions_by_model = await self._get_sessions_by_model()
        
        # Clean up expired sessions
        await self._cleanup_expired_sessions()
        
        # Process each model
        for model_id, sessions in sessions_by_model.items():
            is_preferred = model_id in preferred_models
            
            await self._process_model_sessions(
                model_id, sessions, is_preferred
            )
        
        # For preferred models with no sessions, open one
        for model_id in preferred_models:
            if model_id not in sessions_by_model:
                auto_logger.info("Preferred model has no sessions, opening one",
                               model_id=model_id,
                               event_type="preferred_model_open_session")
                
                try:
                    await self._open_session_for_model(
                        model_id=model_id,
                        model_name=None,
                        model_type="AUTOMATION",
                    )
                    auto_logger.info("Opened session for preferred model",
                                   model_id=model_id,
                                   event_type="preferred_model_session_opened")
                except Exception as e:
                    auto_logger.error("Failed to open session for preferred model",
                                    model_id=model_id,
                                    error=str(e),
                                    event_type="preferred_model_session_open_error")
    
    async def _process_model_sessions(
        self,
        model_id: str,
        sessions: List[SessionData],
        is_preferred: bool
    ) -> None:
        """
        Process sessions for a model according to automation rules.
        
        Preferred model rules:
        - If all utilized -> open another session
        - If unutilized_count > 1 -> close one
        - If unutilized_count == 1 -> do nothing
        
        Non-preferred model rules:
        - If any unutilized -> close one
        """
        process_logger = logger.bind(
            model_id=model_id,
            is_preferred=is_preferred,
            session_count=len(sessions)
        )
        
        # Separate utilized and unutilized
        utilized = [s for s in sessions if s.is_utilized]
        unutilized = [s for s in sessions if not s.is_utilized]
        
        # Filter unutilized by idle grace period
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        grace_threshold = now - timedelta(seconds=settings.SESSION_IDLE_GRACE_SECONDS)
        
        idle_long_enough = [
            s for s in unutilized
            if (s.last_used_at or s.created_at) < grace_threshold
        ]
        
        process_logger.debug("Model session status",
                           utilized_count=len(utilized),
                           unutilized_count=len(unutilized),
                           idle_long_enough=len(idle_long_enough),
                           event_type="model_session_status")
        
        if is_preferred:
            # Preferred model logic
            if len(unutilized) == 0:
                # All utilized - scale up by opening another session
                process_logger.info("All sessions utilized for preferred model, opening another",
                                  event_type="preferred_all_utilized")
                try:
                    await self._open_session_for_model(
                        model_id=model_id,
                        model_name=None,
                        model_type="LLM",
                        user_id=0  # Use fallback private key
                    )
                    process_logger.info("Opened additional session for preferred model",
                                      model_id=model_id,
                                      event_type="preferred_model_scaled_up")
                except Exception as e:
                    process_logger.error("Failed to open additional session for preferred model",
                                       model_id=model_id,
                                       error=str(e),
                                       event_type="preferred_model_scale_up_error")
            elif len(idle_long_enough) > 1:
                # More than one idle - close the most idle
                to_close = max(
                    idle_long_enough,
                    key=lambda s: (now - (s.last_used_at or s.created_at)).total_seconds()
                )
                process_logger.info("Closing excess idle session for preferred model",
                                  session_id=to_close.id,
                                  event_type="preferred_close_excess")
                await self._close_session(to_close)
        else:
            # Non-preferred model logic
            if idle_long_enough:
                # Close the most idle session
                to_close = max(
                    idle_long_enough,
                    key=lambda s: (now - (s.last_used_at or s.created_at)).total_seconds()
                )
                process_logger.info("Closing idle session for non-preferred model",
                                  session_id=to_close.id,
                                  event_type="non_preferred_close_idle")
                await self._close_session(to_close)
    
    async def _cleanup_expired_sessions(self) -> None:
        """Mark expired sessions and attempt to close them on the proxy."""
        expired_sessions = await self._store.get_expired_open()
        
        for session in expired_sessions:
            logger.info("Closing expired session",
                       session_id=session.id,
                       expired_at=session.expires_at.isoformat() if session.expires_at else "n/a",
                       event_type="closing_expired_session")
            
            # Mark as EXPIRED
            await self._store.update_state(session.id, SessionState.EXPIRED)
            
            # Still try to close on proxy router
            try:
                await proxy_router_service.closeSession(session.id)
                
            except Exception as e:
                logger.warning("Error closing expired session on proxy",
                             session_id=session.id,
                             error=str(e),
                             event_type="expired_session_close_error")
        
        if expired_sessions:
            logger.info("Cleaned up expired sessions",
                       count=len(expired_sessions),
                       event_type="expired_sessions_cleaned")
    
    # =========================================================================
    # QUERY HELPERS
    # =========================================================================
    
    async def _get_sessions_by_model(self) -> Dict[str, List[SessionData]]:
        """Get all OPEN sessions grouped by model_id."""
        sessions = await self._store.get_all_open()
        
        by_model: Dict[str, List[SessionData]] = {}
        for session in sessions:
            if session.model_id not in by_model:
                by_model[session.model_id] = []
            by_model[session.model_id].append(session)
        
        return by_model
    
    # =========================================================================
    # SESSION INFO
    # =========================================================================
    
    async def get_session_info(self, session_id: str) -> Optional[SessionData]:
        """Get information about a specific session."""
        return await self._store.get(session_id)
    
    async def get_model_sessions_summary(self, model_id: str) -> Dict[str, Any]:
        """Get a summary of sessions for a model."""
        sessions = await self._store.get_open_for_model(model_id)
        
        return {
            "model_id": model_id,
            "total_open": len(sessions),
            "utilized": len([s for s in sessions if s.is_utilized]),
            "unutilized": len([s for s in sessions if not s.is_utilized]),
            "total_active_requests": sum(s.active_requests for s in sessions),
            "sessions": [
                {
                    "id": s.id,
                    "state": s.state,
                    "active_requests": s.active_requests,
                    "last_used_at": s.last_used_at.isoformat() if s.last_used_at else None,
                    "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                }
                for s in sessions
            ]
        }


# Singleton instance
session_routing_service = SessionRoutingService()
