from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, LargeBinary, JSON, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()

# User model (if not already defined)
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    private_key = relationship("UserPrivateKey", back_populates="user", uselist=False, cascade="all, delete-orphan")


# APIKey model (if not already defined)
class APIKey(Base):
    __tablename__ = "api_keys"
    
    id = Column(Integer, primary_key=True, index=True)
    key_prefix = Column(String, index=True)
    hashed_key = Column(String)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    
    # Relationships
    user = relationship("User", back_populates="api_keys")
    session = relationship("UserSession", back_populates="api_key", uselist=False, cascade="all, delete-orphan")


# UserPrivateKey model (focus of this implementation)
class UserPrivateKey(Base):
    __tablename__ = "user_private_keys"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)  # One private key per user
    encrypted_private_key = Column(LargeBinary)  # Store encrypted key as binary data
    encryption_metadata = Column(JSON)  # Store salt, algorithm info, etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="private_key")


# New model to store Session information associated with API keys
class UserSession(Base):
    __tablename__ = "user_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id"), unique=True)
    session_id = Column(String, index=True)
    model_id = Column(String)  # Store the model/bid ID used to create this session
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    
    # Relationships
    api_key = relationship("APIKey", back_populates="session")
    
    # Only one active session per API key
    __table_args__ = (
        UniqueConstraint('api_key_id', name='unique_api_key_session'),
    ) 