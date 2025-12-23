"""
Session model for blockchain provider sessions.
"""
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Index
from sqlalchemy.orm import relationship
from datetime import datetime

from .base import Base


class Session(Base):
    """Session model for blockchain provider sessions."""
    __tablename__ = "sessions"
    
    id = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id"), nullable=True, index=True)
    model = Column(String, nullable=False)
    type = Column(String, nullable=False)  # "automated" or "manual"
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True, index=True)
    
    user = relationship("User", back_populates="sessions")
    api_key = relationship("APIKey", back_populates="sessions")
    
    # Constraint to enforce one active session per API key
    __table_args__ = (
        Index('sessions_active_api_key_unique', 'api_key_id', 'is_active', 
              unique=True, postgresql_where=is_active.is_(True)),
    )
    
    @property
    def is_expired(self):
        return datetime.utcnow() > self.expires_at

