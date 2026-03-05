"""
User model for authentication and account management.

PII (email, name) lives exclusively in Cognito.  The database only stores
cognito_user_id as the identity key and application-level fields.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime

from .base import Base


class User(Base):
    """User model for authentication and account management."""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    cognito_user_id = Column(String, unique=True, index=True, nullable=False)  # Cognito 'sub' claim
    is_active = Column(Boolean, default=True)
    age_verified = Column(Boolean, default=False, nullable=False)
    age_verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    api_keys = relationship("APIKey", back_populates="user")
    chats = relationship("Chat", back_populates="user", cascade="all, delete-orphan")
    wallet_links = relationship("WalletLink", back_populates="user", cascade="all, delete-orphan")
    wallet_nonces = relationship("WalletNonce", back_populates="user", cascade="all, delete-orphan")

