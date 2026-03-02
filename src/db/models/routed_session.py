"""
RoutedSession model for advanced session routing with state management.

This model supports the Session Routing Service with:
- State-based lifecycle (OPEN, CLOSING, CLOSED, FAILED, EXPIRED)
- Utilization tracking (active_requests counter)
- Model-based routing and grouping
- Preferred model support for automated scaling

Note: Rows are only created after a session is successfully opened (no OPENING state).
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Index
from datetime import datetime, timezone
import enum

from .base import Base


class SessionState(str, enum.Enum):
    """Session lifecycle states."""
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class RoutedSession(Base):
    """
    RoutedSession model for advanced session routing.
    
    Key behaviors:
    - Sessions are grouped by model_id for routing decisions
    - State transitions: OPEN -> CLOSING -> CLOSED (rows created only after successful open)
    - Utilization tracked via active_requests counter
    - last_used_at updated on each request assignment
    """
    __tablename__ = "routed_sessions"
    
    # Primary key - the blockchain session ID
    id = Column(String, primary_key=True)
    
    # Model identification
    model_name = Column(String, nullable=True, index=True)  # Human-readable name (e.g., "llama-3.3-70b")
    model_id = Column(String, nullable=False, index=True)   # Blockchain ID (hex string starting with 0x)
    
    # Session state and lifecycle (stored as String, validated via SessionState enum in Python)
    # Note: Rows are only created after successful session open, so default is OPEN
    state = Column(
        String(20),
        nullable=False,
        default=SessionState.OPEN.value,
        index=True
    )
    
    # Utilization tracking
    active_requests = Column(Integer, nullable=False, default=0)
    
    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    last_used_at = Column(DateTime, nullable=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    
    # Optional tracking
    endpoint = Column(String, nullable=True)  # e.g., "/v1/chat/completions"
    error_reason = Column(String, nullable=True)
    
    # Indexes for efficient queries
    __table_args__ = (
        # Index for finding open sessions by model
        Index('idx_routed_sessions_model_state', 'model_id', 'state'),
        # Index for finding unutilized sessions
        Index('idx_routed_sessions_state_active', 'state', 'active_requests'),
        # Index for expiry cleanup
        Index('idx_routed_sessions_expires', 'expires_at'),
    )
    
    @property
    def is_open(self) -> bool:
        """Check if session is in OPEN state."""
        return self.state == SessionState.OPEN.value
    
    @property
    def is_utilized(self) -> bool:
        """Check if session is currently utilized (has active requests)."""
        return self.active_requests > 0
    
    @property
    def is_expired(self) -> bool:
        """Check if session has expired based on expires_at."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return now > self.expires_at
    
    def __repr__(self) -> str:
        return f"<RoutedSession(id={self.id}, model_id={self.model_id}, state={self.state}, active_requests={self.active_requests})>"

