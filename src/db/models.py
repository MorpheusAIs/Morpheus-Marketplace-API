from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, LargeBinary, JSON, UniqueConstraint, Index, TEXT, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
from sqlalchemy.sql import func
import enum

Base = declarative_base()

# User model (if not already defined)
class User(Base):
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
    sessions = relationship("Session", back_populates="api_key")


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


# User Automation Settings for session automation
class UserAutomationSettings(Base):
    __tablename__ = "user_automation_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)  # One automation setting per user
    is_enabled = Column(Boolean, default=False)  # Whether automation is enabled for this user (disabled by default)
    session_duration = Column(Integer, default=3600)  # Default session duration in seconds
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="automation_settings")

# Add Delegation Model
class Delegation(Base):
    __tablename__ = "delegations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    delegate_address = Column(String, nullable=False, index=True)
    # Store the signed delegation object (EIP-712 structure + signature) as JSON or Text
    # Using TEXT might be simpler initially if the structure isn't fixed
    signed_delegation_data = Column(TEXT, nullable=False)
    expiry = Column(DateTime, nullable=True) # Optional expiry from delegation
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(Boolean, default=True, index=True)

    user = relationship("User", back_populates="delegations")

class Session(Base):
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


# Enum for message roles
class MessageRole(enum.Enum):
    user = "user"
    assistant = "assistant"


# Chat model for conversation history
class Chat(Base):
    __tablename__ = "chats"
    
    id = Column(String, primary_key=True)  # UUID string
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_archived = Column(Boolean, default=False)
    
    # Relationships
    user = relationship("User", back_populates="chats")
    messages = relationship("Message", back_populates="chat", cascade="all, delete-orphan")
    
    # Index for efficient queries
    __table_args__ = (
        Index('ix_chats_user_id_updated_at', 'user_id', 'updated_at'),
    )


# Message model for individual chat messages
class Message(Base):
    __tablename__ = "messages"
    
    id = Column(String, primary_key=True)  # UUID string
    chat_id = Column(String, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    role = Column(Enum(MessageRole), nullable=False)
    content = Column(TEXT, nullable=False)
    sequence = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    tokens = Column(Integer, nullable=True)  # Token count for billing/analytics
    
    # Relationships
    chat = relationship("Chat", back_populates="messages")
    
    # Index for efficient queries
    __table_args__ = (
        Index('ix_messages_chat_id_sequence', 'chat_id', 'sequence'),
    ) 