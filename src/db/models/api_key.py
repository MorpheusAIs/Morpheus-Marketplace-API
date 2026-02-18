"""
APIKey model for API authentication.
"""
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, TEXT
from sqlalchemy.orm import relationship
from datetime import datetime

from .base import Base


class APIKey(Base):
    """API Key model for programmatic access authentication."""
    __tablename__ = "api_keys"
    
    id = Column(Integer, primary_key=True, index=True)
    key_prefix = Column(String, index=True)
    hashed_key = Column(String)  # Keep for backward compatibility and verification
    encrypted_key = Column(TEXT, nullable=True)  # New encrypted storage
    encryption_version = Column(Integer, default=1, index=True)  # For future algorithm updates
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False, index=True)  # User-defined default key
    
    # Relationships
    user = relationship("User", back_populates="api_keys")

