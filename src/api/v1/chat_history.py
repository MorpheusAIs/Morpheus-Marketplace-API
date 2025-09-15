"""
Chat history management endpoints.
Provides REST API for managing chat conversations and messages.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime

from src.db.database import get_db
from src.db.models import User, MessageRole
from src.dependencies import get_api_key_user, get_current_user, CurrentUser
from src.crud import chat as chat_crud


router = APIRouter()


# Dependency to get current user from API key authentication only
# This ensures consistency with chat completions endpoint which also uses API key
async def get_current_user_api_key_only(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_api_key_user)
) -> User:
    """
    Get current user from API key authentication only.
    This ensures chat history operations use the same auth as chat completions.
    """
    return user


# Pydantic models for request/response
class MessageCreate(BaseModel):
    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")
    tokens: Optional[int] = Field(None, description="Token count for billing")


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    sequence: int
    created_at: datetime
    tokens: Optional[int] = None

    class Config:
        from_attributes = True


class ChatCreate(BaseModel):
    title: str = Field(..., max_length=200, description="Chat title")


class ChatUpdate(BaseModel):
    title: str = Field(..., max_length=200, description="Updated chat title")


class ChatResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: Optional[int] = None

    class Config:
        from_attributes = True


class ChatDetailResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: List[MessageResponse] = []

    class Config:
        from_attributes = True


# Chat endpoints
@router.post("/chats", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def create_chat(
    chat_data: ChatCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_api_key_only)
):
    """Create a new chat conversation."""
    chat = await chat_crud.create_chat(db, current_user.id, chat_data.title)
    return ChatResponse(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        message_count=0
    )


@router.get("/chats", response_model=List[ChatResponse])
async def get_user_chats(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_api_key_only)
):
    """Get all chats for the current user."""
    chats = await chat_crud.get_user_chats(db, current_user.id, skip, limit)
    
    # Convert to response format with message count
    chat_responses = []
    for chat in chats:
        message_count = len(chat.messages) if hasattr(chat, 'messages') else 0
        chat_responses.append(ChatResponse(
            id=chat.id,
            title=chat.title,
            created_at=chat.created_at,
            updated_at=chat.updated_at,
            message_count=message_count
        ))
    
    return chat_responses


@router.get("/chats/{chat_id}", response_model=ChatDetailResponse)
async def get_chat(
    chat_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_api_key_only)
):
    """Get a specific chat with all messages."""
    chat = await chat_crud.get_chat_by_id(db, chat_id, current_user.id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    # Convert messages to response format
    messages = [
        MessageResponse(
            id=msg.id,
            role=msg.role.value,
            content=msg.content,
            sequence=msg.sequence,
            created_at=msg.created_at,
            tokens=msg.tokens
        )
        for msg in sorted(chat.messages, key=lambda x: x.sequence)
    ]
    
    return ChatDetailResponse(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        messages=messages
    )


@router.put("/chats/{chat_id}", response_model=ChatResponse)
async def update_chat(
    chat_id: str,
    chat_data: ChatUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_api_key_only)
):
    """Update chat title."""
    chat = await chat_crud.update_chat_title(db, chat_id, current_user.id, chat_data.title)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    return ChatResponse(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at
    )


@router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: str,
    archive_only: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_api_key_only)
):
    """Delete or archive a chat."""
    if archive_only:
        success = await chat_crud.archive_chat(db, chat_id, current_user.id)
    else:
        success = await chat_crud.delete_chat(db, chat_id, current_user.id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Chat not found")


# Message endpoints
@router.post("/chats/{chat_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def create_message(
    chat_id: str,
    message_data: MessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_api_key_only)
):
    """Add a message to a chat."""
    # Verify chat exists and user owns it
    chat = await chat_crud.get_chat_by_user_and_ensure_ownership(db, chat_id, current_user.id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    # Validate role
    try:
        role = MessageRole(message_data.role)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid role. Must be 'user' or 'assistant'")
    
    # Get next sequence number
    existing_messages = await chat_crud.get_chat_messages(db, chat_id, current_user.id)
    next_sequence = len(existing_messages) + 1
    
    # Create message
    message = await chat_crud.create_message(
        db, chat_id, role, message_data.content, next_sequence, message_data.tokens
    )
    
    return MessageResponse(
        id=message.id,
        role=message.role.value,
        content=message.content,
        sequence=message.sequence,
        created_at=message.created_at,
        tokens=message.tokens
    )


@router.get("/chats/{chat_id}/messages", response_model=List[MessageResponse])
async def get_chat_messages(
    chat_id: str,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_api_key_only)
):
    """Get messages for a specific chat."""
    messages = await chat_crud.get_chat_messages(db, chat_id, current_user.id, skip, limit)
    
    return [
        MessageResponse(
            id=msg.id,
            role=msg.role.value,
            content=msg.content,
            sequence=msg.sequence,
            created_at=msg.created_at,
            tokens=msg.tokens
        )
        for msg in messages
    ]


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_api_key_only)
):
    """Delete a specific message."""
    success = await chat_crud.delete_message(db, message_id, current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Message not found")

# Export router
chat_history_router = router
