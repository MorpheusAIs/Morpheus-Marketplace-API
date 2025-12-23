"""
UserPrivateKey model for encrypted blockchain private key storage.
"""
from sqlalchemy import Column, Integer, ForeignKey, DateTime, LargeBinary, JSON
from sqlalchemy.orm import relationship
from datetime import datetime

from .base import Base


class UserPrivateKey(Base):
    """Model for storing encrypted blockchain private keys."""
    __tablename__ = "user_private_keys"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)  # One private key per user
    encrypted_private_key = Column(LargeBinary)  # Store encrypted key as binary data
    encryption_metadata = Column(JSON)  # Store salt, algorithm info, etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="private_key")

