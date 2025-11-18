from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema
from io import BytesIO

from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any, Union, Optional, Annotated
import asyncio
import time
import uuid
from datetime import datetime, timezone


from ....schemas import openai as openai_schemas
from ....crud import session as session_crud
from ....crud import api_key as api_key_crud
from ....core.config import settings
from ....services import session_service
from ....services import proxy_router_service
from ....db.database import get_db
from ....dependencies import get_api_key_user, get_current_api_key
from ....db.models import User, APIKey
from ....core.logging_config import get_api_logger

router = APIRouter(tags=["Audio"])

logger = get_api_logger()


class AudioSpeechRequest(BaseModel):
    """Request model for audio speech endpoint"""
    input: str = Field(..., description="Text to convert to speech")
    voice: Optional[str] = Field("af_alloy", description="Voice to use for speech generation")
    response_format: Optional[str] = Field("mp3", description="Audio format: mp3, opus, aac, flac, wav, pcm")
    speed: Optional[float] = Field(1, description="Speech speed")
    session_id: Optional[str] = Field(None, description="Session ID (auto-created if not provided)")
    model: Optional[str] = Field(None, description="Model name")

@router.post("/audio/transcriptions")
async def create_audio_transcription(
    file: Annotated[
        UploadFile | SkipJsonSchema[None], File(description="Audio file to transcribe")
    ] = None,
    s3_presigned_url: Optional[str] = Form(default=None, description="Pre-signed S3 URL as alternative to file upload"),
    prompt: Optional[str] = Form(default=None, description="Optional text to guide transcription"),
    temperature: Optional[float] = Form(default=None, description="Sampling temperature (0.0-1.0)"),
    language: Optional[str] = Form(default=None, description="Language code (e.g., 'en')"),
    response_format: Optional[str] = Form(default=None, description="Response format: json, text, srt, verbose_json, vtt"),
    timestamp_granularities: Optional[str] = Form(default=None, description="Comma-separated: word, segment"),
    output_content: Optional[str] = Form(default=None, description="Output content type"),
    enable_diarization: Optional[bool] = Form(default=False, description="Enable speaker diarization"),
    session_id: Optional[str] = Form(default=None, description="Session ID (auto-created if not provided)"),
    model: Optional[str] = Form(default=None, description="Model name"),
    user: User = Depends(get_api_key_user),
    db_api_key: APIKey = Depends(get_current_api_key),
):
    """
    Transcribe audio file to text.
    
    This endpoint transcribes audio files using the Morpheus Network providers.
    It automatically manages sessions and routes requests to the appropriate transcription model.
    
    Supports both file upload and S3 pre-signed URLs.
    Returns JSON or plain text responses based on response_format parameter.
    """
    request_id = str(uuid.uuid4())[:8]  # Generate short request ID for tracing
    transcription_logger = logger.bind(
        endpoint="create_audio_transcription",
        user_id=user.id,
        model=model,
        request_id=request_id
    )
    
    transcription_logger.info(
        "Audio transcription request received",
        model=model,
        response_format=response_format,
        language=language,
        has_file=file is not None,
        has_s3_url=s3_presigned_url is not None,
        event_type="audio_transcription_request_start"
    )
    
    try:
        # Validate that either file or s3_presigned_url is provided
        if not file and not s3_presigned_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either 'file' or 's3_presigned_url' must be provided"
            )
        
        requested_model = model
        
        # Get or create session if not provided
        if not session_id:
            try:
                transcription_logger.info(
                    "No session_id in request, attempting to retrieve or create one",
                    request_id=request_id,
                    api_key_id=db_api_key.id,
                    requested_model=requested_model,
                    event_type="session_lookup_start"
                )
                async with get_db() as db:
                    session = await session_service.get_session_for_api_key(
                        db, db_api_key.id, user.id, requested_model, model_type='STT'
                    )
                    if session:
                        session_id = session.id
                
                if session_id:
                    transcription_logger.info(
                        "Session retrieved successfully",
                        request_id=request_id,
                        session_id=session_id,
                        event_type="session_lookup_success"
                    )
            except Exception as e:
                transcription_logger.error(
                    "Error in session handling",
                    request_id=request_id,
                    error=str(e),
                    event_type="session_handling_error",
                    exc_info=True
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Error handling session: {str(e)}"
                )
        
        # If we still don't have a session_id, return an error
        if not session_id:
            transcription_logger.error(
                "No session ID after all attempts",
                request_id=request_id,
                api_key_id=db_api_key.id,
                requested_model=requested_model,
                event_type="no_session_available"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No session ID provided in request and no active session found for API key"
            )
        
        # Parse timestamp_granularities if provided
        parsed_granularities = None
        if timestamp_granularities:
            parsed_granularities = [g.strip() for g in timestamp_granularities.split(',')]
        
        try:
            response = await proxy_router_service.audio_transcription(
                session_id=session_id,
                file=file,
                s3_presigned_url=s3_presigned_url,
                prompt=prompt,
                temperature=temperature,
                language=language,
                response_format=response_format,
                timestamp_granularities=parsed_granularities,
                output_content=output_content,
                enable_diarization=enable_diarization
            )

            
            transcription_logger.info(
                "Proxy-router response received",
                status_code=response.status_code,
                event_type="proxy_response_received"
            )
            
            if response.status_code == 200:
                # Handle different response formats
                if response_format == "text":
                    transcription_logger.info(
                        "Successfully processed audio transcription (text)",
                        session_id=session_id,
                        event_type="transcription_success"
                    )
                    return PlainTextResponse(content=response.text)
                else:
                    response_data = response.json()
                    transcription_logger.info(
                        "Successfully processed audio transcription (json)",
                        session_id=session_id,
                        event_type="transcription_success"
                    )
                    return JSONResponse(content=response_data)
            else:
                transcription_logger.error(
                    "Proxy-router error",
                    status_code=response.status_code,
                    error_response=response.text,
                    session_id=session_id,
                    event_type="proxy_error"
                )
                
                # Try to parse error response
                try:
                    error_data = response.json()
                    error_message = error_data.get("detail", error_data.get("error", response.text))
                except:
                    error_message = response.text
                
                return JSONResponse(
                    status_code=response.status_code,
                    content={
                        "error": {
                            "message": error_message,
                            "type": "proxy_error",
                        }
                    }
                )
        
        except proxy_router_service.ProxyRouterServiceError as e:
            transcription_logger.error(
                "Proxy router service error",
                error=str(e),
                status_code=e.status_code,
                error_type=e.error_type,
                session_id=session_id,
                event_type="proxy_service_error"
            )
            return JSONResponse(
                status_code=e.get_http_status_code(),
                content={
                    "error": {
                        "message": e.message,
                        "type": "proxy_error",
                    }
                }
            )
    
    except HTTPException:
        raise
    except Exception as e:
        transcription_logger.error(
            "Unexpected error in audio transcription endpoint",
            error=str(e),
            model=model,
            event_type="transcription_unexpected_error",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.post(
    "/audio/speech",
    responses={
        200: {
            "content": {
                "audio/mpeg": { "schema": { "type": "string", "format": "binary" } },
                "audio/opus": { "schema": { "type": "string", "format": "binary" } },
                "audio/aac": { "schema": { "type": "string", "format": "binary" } },
                "audio/flac": { "schema": { "type": "string", "format": "binary" } },
                "audio/wav": { "schema": { "type": "string", "format": "binary" } },
                "audio/pcm": { "schema": { "type": "string", "format": "binary" } }
            },
            "description": "Binary audio file"
        }
    }
)
async def create_audio_speech(
    request_data: AudioSpeechRequest,
    request: Request,
    user: User = Depends(get_api_key_user),
    db_api_key: APIKey = Depends(get_current_api_key),
):
    """
    Generate audio speech from text.
    
    This endpoint converts text to speech using the Morpheus Network providers.
    It automatically manages sessions and routes requests to the appropriate TTS model.
    
    Returns binary audio data in the specified format.
    
    **Note:** Swagger UI may not be able to play the audio directly. 
    To test, click "Download" and play the file in your media player, 
    or use curl to save the audio file.
    """
    # Extract parameters from request
    input = request_data.input
    voice = request_data.voice
    response_format = request_data.response_format
    speed = request_data.speed
    session_id = request_data.session_id
    model = request_data.model
    
    request_id = str(uuid.uuid4())[:8]  # Generate short request ID for tracing
    speech_logger = logger.bind(
        endpoint="create_audio_speech",
        user_id=user.id,
        model=model,
        request_id=request_id
    )
    
    speech_logger.info(
        "Audio speech request received",
        model=model,
        response_format=response_format,
        voice=voice,
        speed=speed,
        event_type="audio_speech_request_start"
    )
    
    try:        
        requested_model = model
        
        # Get or create session if not provided
        if not session_id:
            try:
                speech_logger.info(
                    "No session_id in request, attempting to retrieve or create one",
                    request_id=request_id,
                    api_key_id=db_api_key.id,
                    requested_model=requested_model,
                    event_type="session_lookup_start"
                )
                async with get_db() as db:
                    session = await session_service.get_session_for_api_key(
                        db, db_api_key.id, user.id, requested_model, model_type='TTS'
                    )
                    if session:
                        session_id = session.id
                
                if session_id:
                    speech_logger.info(
                        "Session retrieved successfully",
                        request_id=request_id,
                        session_id=session_id,
                        event_type="session_lookup_success"
                    )
            except Exception as e:
                speech_logger.error(
                    "Error in session handling",
                    request_id=request_id,
                    error=str(e),
                    event_type="session_handling_error",
                    exc_info=True
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Error handling session: {str(e)}"
                )
        
        # If we still don't have a session_id, return an error
        if not session_id:
            speech_logger.error(
                "No session ID after all attempts",
                request_id=request_id,
                api_key_id=db_api_key.id,
                requested_model=requested_model,
                event_type="no_session_available"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No session ID provided in request and no active session found for API key"
            )
        
        try:
            # Call proxy router service for audio speech
            response = await proxy_router_service.audio_speech(
                session_id=session_id,
                input=input,
                voice=voice,
                response_format=response_format,
                speed=speed
            )
            
            speech_logger.info(
                "Proxy-router response received",
                status_code=response.status_code,
                content_type_received=response.headers.get("content-type"),
                content_length_received=len(response.content),
                event_type="proxy_response_received"
            )
            
            if response.status_code == 200:
                # Return binary audio data with appropriate content type
                content_type_map = {
                    "mp3": "audio/mpeg",
                    "opus": "audio/opus",
                    "aac": "audio/aac",
                    "flac": "audio/flac",
                    "wav": "audio/wav",
                    "pcm": "audio/pcm"
                }
                # Default to mp3 if no format specified
                format_used = response_format or "mp3"
                content_type = content_type_map.get(format_used, "audio/mpeg")
                
                audio_content = response.content
                audio_length = len(audio_content)
                
                speech_logger.info(
                    "Successfully processed audio speech",
                    session_id=session_id,
                    content_type=content_type,
                    content_length=audio_length,
                    format_requested=response_format,
                    format_used=format_used,
                    event_type="speech_success"
                )
                
                return StreamingResponse(
                    content=BytesIO(audio_content),
                    media_type=content_type,
                    headers={
                        "Content-Disposition": f'attachment; filename="speech.{format_used}"',
                        "Content-Length": str(audio_length),
                    }
                )
            else:
                speech_logger.error(
                    "Proxy-router error",
                    status_code=response.status_code,
                    error_response=response.text,
                    session_id=session_id,
                    event_type="proxy_error"
                )
                
                # Try to parse error response
                try:
                    error_data = response.json()
                    error_message = error_data.get("detail", error_data.get("error", response.text))
                except:
                    error_message = response.text
                
                return JSONResponse(
                    status_code=response.status_code,
                    content={
                        "error": {
                            "message": error_message,
                            "type": "proxy_error",
                        }
                    }
                )
        
        except proxy_router_service.ProxyRouterServiceError as e:
            speech_logger.error(
                "Proxy router service error",
                error=str(e),
                status_code=e.status_code,
                error_type=e.error_type,
                session_id=session_id,
                event_type="proxy_service_error"
            )
            return JSONResponse(
                status_code=e.get_http_status_code(),
                content={
                    "error": {
                        "message": e.message,
                        "type": "proxy_error",
                    }
                }
            )
    
    except HTTPException:
        raise
    except Exception as e:
        speech_logger.error(
            "Unexpected error in audio speech endpoint",
            error=str(e),
            model=model,
            event_type="speech_unexpected_error",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

