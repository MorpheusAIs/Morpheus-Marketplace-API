"""
Chat and Message models for conversation history.
"""
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, TEXT, Enum, Index
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from .base import Base


class MessageRole(enum.Enum):
    """Enum for message roles."""
    user = "user"
    assistant = "assistant"


class Chat(Base):
    """Chat model for conversation history."""
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


class Message(Base):
    """Message model for individual chat messages."""
    __tablename__ = "messages"
    
    id = Column(String, primary_key=True)  # UUID string
    chat_id = Column(String, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    role = Column(Enum(MessageRole, name='message_role'), nullable=False)  # Match migration-created enum name
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

