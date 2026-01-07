"""
User model for authentication and account management.
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
    email = Column(String, unique=True, index=True, nullable=False)  # From Cognito token
    name = Column(String, nullable=True)  # From Cognito token (given_name/family_name)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    api_keys = relationship("APIKey", back_populates="user")
    sessions = relationship("Session", back_populates="user")
    private_key = relationship("UserPrivateKey", back_populates="user", uselist=False, cascade="all, delete-orphan")
    automation_settings = relationship("UserAutomationSettings", back_populates="user", uselist=False, cascade="all, delete-orphan")
    delegations = relationship("Delegation", back_populates="user", cascade="all, delete-orphan")
    chats = relationship("Chat", back_populates="user", cascade="all, delete-orphan")

