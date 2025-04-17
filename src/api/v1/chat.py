# Placeholder for OpenAI chat completion routes 

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List, Dict, Any
import json
import time
import uuid
import asyncio

from ...dependencies import get_api_key_user
from ...db.database import get_db
from ...db.models import User
from ...schemas import openai as openai_schemas
from ...crud import private_key as private_key_crud
from ...services.model_mapper import model_mapper

router = APIRouter(tags=["chat"])


@router.post("/completions", response_model=openai_schemas.ChatCompletionResponse)
async def create_chat_completion(
    request: openai_schemas.ChatCompletionRequest,
    user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a chat completion.
    
    This implementation:
    1. Validates the user has a registered private key
    2. Maps the OpenAI model ID to a blockchain model ID using Redis cache
    3. Creates a mock response (in production, would call proxy-router)
    """
    # Check if user has a private key
    private_key = await private_key_crud.get_decrypted_private_key(db, user.id)
    if not private_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No private key found. Please register a private key first using POST /auth/private-key."
        )
    
    # Map the OpenAI model ID to a blockchain model ID
    blockchain_model_id = await model_mapper.get_blockchain_model_id(request.model)
    if not blockchain_model_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{request.model}' not supported or mapping not found."
        )
    
    # Handle streaming requests
    if request.stream:
        return await create_streaming_chat_completion(request, user, private_key, blockchain_model_id)
    
    # For non-streaming, create a mock response
    return create_mock_completion_response(request, blockchain_model_id)


async def create_streaming_chat_completion(
    request: openai_schemas.ChatCompletionRequest,
    user: User,
    private_key: str,
    blockchain_model_id: str
):
    """
    Create a streaming chat completion response.
    
    This is a placeholder that streams mock data.
    In production, this would stream responses from the proxy-router.
    """
    # This is a placeholder implementation that simulates streaming for development
    async def fake_stream_generator():
        # Generate a completion ID
        completion_id = f"chatcmpl-{uuid.uuid4()}"
        created_time = int(time.time())
        
        # Extract the last user message content for our mock response
        last_user_msg = ""
        for msg in reversed(request.messages):
            if msg.role == openai_schemas.ChatMessageRole.USER:
                last_user_msg = msg.content
                break
        
        # Generate a mock response
        mock_response = f"This is a mock response from blockchain model {blockchain_model_id} to: {last_user_msg}"
        
        # Stream the response token by token
        content_so_far = ""
        
        # First chunk - role
        first_chunk = openai_schemas.ChatCompletionChunk(
            id=completion_id,
            created=created_time,
            model=request.model,
            choices=[
                openai_schemas.ChatCompletionChunkChoice(
                    index=0,
                    delta=openai_schemas.ChatCompletionChunkDelta(
                        role=openai_schemas.ChatMessageRole.ASSISTANT
                    ),
                    finish_reason=None
                )
            ]
        )
        yield f"data: {first_chunk.json()}\n\n"
        await asyncio.sleep(0.05)
        
        # Stream content tokens
        for i, char in enumerate(mock_response):
            content_so_far += char
            chunk = openai_schemas.ChatCompletionChunk(
                id=completion_id,
                created=created_time,
                model=request.model,
                choices=[
                    openai_schemas.ChatCompletionChunkChoice(
                        index=0,
                        delta=openai_schemas.ChatCompletionChunkDelta(
                            content=char
                        ),
                        finish_reason=None
                    )
                ]
            )
            yield f"data: {chunk.json()}\n\n"
            await asyncio.sleep(0.02)  # Simulate token generation time
        
        # Final chunk with finish_reason
        final_chunk = openai_schemas.ChatCompletionChunk(
            id=completion_id,
            created=created_time,
            model=request.model,
            choices=[
                openai_schemas.ChatCompletionChunkChoice(
                    index=0,
                    delta=openai_schemas.ChatCompletionChunkDelta(),
                    finish_reason="stop"
                )
            ]
        )
        yield f"data: {final_chunk.json()}\n\n"
        
        # End the stream
        yield "data: [DONE]\n\n"
    
    return StreamingResponse(
        fake_stream_generator(),
        media_type="text/event-stream"
    )


def create_mock_completion_response(
    request: openai_schemas.ChatCompletionRequest,
    blockchain_model_id: str
):
    """Create a mock completion response for development"""
    # Extract the last user message content for our mock response
    last_user_msg = ""
    for msg in reversed(request.messages):
        if msg.role == openai_schemas.ChatMessageRole.USER:
            last_user_msg = msg.content
            break
    
    # Generate a mock response
    mock_response = f"This is a mock response from blockchain model {blockchain_model_id} to: {last_user_msg}"
    
    # Create and return the response
    return openai_schemas.ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4()}",
        created=int(time.time()),
        model=request.model,
        choices=[
            openai_schemas.ChatCompletionChoice(
                index=0,
                message=openai_schemas.ChatMessage(
                    role=openai_schemas.ChatMessageRole.ASSISTANT,
                    content=mock_response
                ),
                finish_reason="stop"
            )
        ],
        usage=openai_schemas.ChatCompletionResponseUsage(
            prompt_tokens=len(" ".join([m.content for m in request.messages]).split()),
            completion_tokens=len(mock_response.split()),
            total_tokens=len(" ".join([m.content for m in request.messages]).split()) + len(mock_response.split())
        )
    ) 