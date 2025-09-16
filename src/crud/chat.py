"""
CRUD operations for chat and message management.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func
from sqlalchemy.orm import selectinload
from typing import List, Optional
import uuid
from datetime import datetime

from src.db.models import Chat, Message, MessageRole, User


async def create_chat(db: AsyncSession, user_id: int, title: str) -> Chat:
    """Create a new chat for a user."""
    chat = Chat(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title=title,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(chat)
    await db.commit()
    await db.refresh(chat)
    return chat


async def get_chat_by_id(db: AsyncSession, chat_id: str, user_id: int) -> Optional[Chat]:
    """Get a chat by ID, ensuring it belongs to the user."""
    result = await db.execute(
        select(Chat)
        .options(selectinload(Chat.messages))
        .where(Chat.id == chat_id, Chat.user_id == user_id, Chat.is_archived == False)
    )
    return result.scalar_one_or_none()


async def get_user_chats(db: AsyncSession, user_id: int, skip: int = 0, limit: int = 50) -> List[Chat]:
    """Get all chats for a user, ordered by updated_at desc."""
    result = await db.execute(
        select(Chat)
        .where(Chat.user_id == user_id, Chat.is_archived == False)
        .order_by(Chat.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars())


async def get_chat_message_count(db: AsyncSession, chat_id: str) -> int:
    """Get the number of messages in a chat."""
    result = await db.execute(
        select(func.count(Message.id))
        .where(Message.chat_id == chat_id)
    )
    return result.scalar() or 0


async def update_chat_title(db: AsyncSession, chat_id: str, user_id: int, title: str) -> Optional[Chat]:
    """Update chat title."""
    result = await db.execute(
        update(Chat)
        .where(Chat.id == chat_id, Chat.user_id == user_id)
        .values(title=title, updated_at=datetime.utcnow())
        .returning(Chat)
    )
    chat = result.scalar_one_or_none()
    if chat:
        await db.commit()
    return chat


async def archive_chat(db: AsyncSession, chat_id: str, user_id: int) -> bool:
    """Archive a chat (soft delete)."""
    result = await db.execute(
        update(Chat)
        .where(Chat.id == chat_id, Chat.user_id == user_id)
        .values(is_archived=True, updated_at=datetime.utcnow())
    )
    if result.rowcount > 0:
        await db.commit()
        return True
    return False


async def delete_chat(db: AsyncSession, chat_id: str, user_id: int) -> bool:
    """Permanently delete a chat and all its messages."""
    result = await db.execute(
        delete(Chat)
        .where(Chat.id == chat_id, Chat.user_id == user_id)
    )
    if result.rowcount > 0:
        await db.commit()
        return True
    return False


# Message CRUD operations
async def create_message(
    db: AsyncSession, 
    chat_id: str, 
    role: MessageRole, 
    content: str, 
    sequence: int,
    tokens: Optional[int] = None
) -> Message:
    """Create a new message in a chat."""
    message = Message(
        id=str(uuid.uuid4()),
        chat_id=chat_id,
        role=role,
        content=content,
        sequence=sequence,
        tokens=tokens,
        created_at=datetime.utcnow()
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    
    # Update chat's updated_at timestamp
    await db.execute(
        update(Chat)
        .where(Chat.id == chat_id)
        .values(updated_at=datetime.utcnow())
    )
    await db.commit()
    
    return message


async def get_chat_messages(
    db: AsyncSession, 
    chat_id: str, 
    user_id: int,
    skip: int = 0, 
    limit: int = 100
) -> List[Message]:
    """Get messages for a chat, ensuring user owns the chat."""
    # First verify user owns the chat
    chat_result = await db.execute(
        select(Chat.id)
        .where(Chat.id == chat_id, Chat.user_id == user_id, Chat.is_archived == False)
    )
    if not chat_result.scalar_one_or_none():
        return []
    
    # Get messages
    result = await db.execute(
        select(Message)
        .where(Message.chat_id == chat_id)
        .order_by(Message.sequence.asc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


async def get_message_by_id(db: AsyncSession, message_id: str, user_id: int) -> Optional[Message]:
    """Get a message by ID, ensuring user owns the chat."""
    result = await db.execute(
        select(Message)
        .join(Chat)
        .where(Message.id == message_id, Chat.user_id == user_id, Chat.is_archived == False)
    )
    return result.scalar_one_or_none()


async def delete_message(db: AsyncSession, message_id: str, user_id: int) -> bool:
    """Delete a message, ensuring user owns the chat."""
    result = await db.execute(
        delete(Message)
        .where(
            Message.id == message_id,
            Message.chat_id.in_(
                select(Chat.id).where(Chat.user_id == user_id, Chat.is_archived == False)
            )
        )
    )
    if result.rowcount > 0:
        await db.commit()
        return True
    return False


async def get_chat_by_user_and_ensure_ownership(db: AsyncSession, chat_id: str, user_id: int) -> Optional[Chat]:
    """Utility function to get chat and ensure user ownership."""
    result = await db.execute(
        select(Chat)
        .where(Chat.id == chat_id, Chat.user_id == user_id, Chat.is_archived == False)
    )
    return result.scalar_one_or_none()
