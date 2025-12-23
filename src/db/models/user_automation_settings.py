"""
UserAutomationSettings model for session automation configuration.
"""
from sqlalchemy import Column, Integer, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime

from .base import Base


class UserAutomationSettings(Base):
    """User Automation Settings for session automation."""
    __tablename__ = "user_automation_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)  # One automation setting per user
    is_enabled = Column(Boolean, default=False)  # Whether automation is enabled for this user (disabled by default)
    session_duration = Column(Integer, default=3600)  # Default session duration in seconds
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="automation_settings")

