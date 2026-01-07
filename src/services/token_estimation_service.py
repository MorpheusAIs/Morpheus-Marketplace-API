"""
Token estimation service for estimating input and output tokens from request bodies.

Provides estimates for different model types (LLM, embeddings, TTS, STT).
"""

from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
import json


@dataclass
class TokenEstimate:
    """Estimated token counts for a request."""
    input_tokens: int
    output_tokens: int
    
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# Average characters per token (rough approximation)
CHARS_PER_TOKEN = 4

# Default max tokens by model type
DEFAULT_MAX_TOKENS = {
    "LLM": 2048,
    "EMBEDDINGS": 0,  # Embeddings don't produce output tokens
    "TTS": 0,  # TTS produces audio, not tokens
    "STT": 500,  # STT produces text from audio
}


class TokenEstimationService:
    """
    Service for estimating token counts from request bodies.
    
    Usage:
        estimator = TokenEstimationService()
        estimate = estimator.estimate(request_body, model_type="LLM")
    """
    
    def estimate(
        self,
        request_body: Dict[str, Any],
        model_type: str = "LLM",
    ) -> TokenEstimate:
        """
        Estimate input and output tokens for a request.
        
        Args:
            request_body: The request body (parsed JSON)
            model_type: Type of model ("LLM", "EMBEDDINGS", "TTS", "STT")
            
        Returns:
            TokenEstimate with input_tokens and output_tokens
        """
        model_type = model_type.upper()
        
        if model_type == "LLM":
            return self._estimate_llm(request_body)
        elif model_type == "EMBEDDINGS":
            return self._estimate_embeddings(request_body)
        elif model_type == "TTS":
            return self._estimate_tts(request_body)
        elif model_type == "STT":
            return self._estimate_stt(request_body)
        else:
            # Default to LLM estimation
            return self._estimate_llm(request_body)
    
    def _estimate_llm(self, request_body: Dict[str, Any]) -> TokenEstimate:
        """Estimate tokens for LLM chat/completion requests."""
        # Estimate input tokens from messages
        messages = request_body.get("messages", [])
        input_text = json.dumps(messages)
        input_tokens = len(input_text) // CHARS_PER_TOKEN
        
        # Add tokens for system prompt if separate
        if "system" in request_body:
            input_tokens += len(request_body["system"]) // CHARS_PER_TOKEN
        
        # Add tokens for tools if present
        if "tools" in request_body:
            tools_text = json.dumps(request_body["tools"])
            input_tokens += len(tools_text) // CHARS_PER_TOKEN
        
        # Get max_tokens from request or use default
        max_tokens = request_body.get("max_tokens")
        if max_tokens is not None:
            output_tokens = max_tokens
        else:
            output_tokens = DEFAULT_MAX_TOKENS["LLM"]
        
        return TokenEstimate(
            input_tokens=max(input_tokens, 1),
            output_tokens=output_tokens,
        )
    
    def _estimate_embeddings(self, request_body: Dict[str, Any]) -> TokenEstimate:
        """Estimate tokens for embedding requests."""
        # Embeddings can take input as string or list of strings
        input_data = request_body.get("input", "")
        
        if isinstance(input_data, str):
            input_text = input_data
        elif isinstance(input_data, list):
            input_text = " ".join(str(item) for item in input_data)
        else:
            input_text = str(input_data)
        
        input_tokens = len(input_text) // CHARS_PER_TOKEN
        
        # Embeddings don't produce output tokens (they produce vectors)
        return TokenEstimate(
            input_tokens=max(input_tokens, 1),
            output_tokens=0,
        )
    
    def _estimate_tts(self, request_body: Dict[str, Any]) -> TokenEstimate:
        """Estimate tokens for text-to-speech requests."""
        # TTS input is the text to synthesize
        input_text = request_body.get("input", "")
        input_tokens = len(input_text) // CHARS_PER_TOKEN
        
        # TTS doesn't produce text tokens (produces audio)
        return TokenEstimate(
            input_tokens=max(input_tokens, 1),
            output_tokens=0,
        )
    
    def _estimate_stt(self, request_body: Dict[str, Any]) -> TokenEstimate:
        """Estimate tokens for speech-to-text requests."""
        # STT input is audio, which we can't easily estimate
        # Use a reasonable default based on typical audio duration
        # Assume ~150 words per minute, ~1.5 tokens per word
        
        # If duration hint is provided, use it
        duration_seconds = request_body.get("duration", 60)  # Default 1 minute
        words_per_minute = 150
        tokens_per_word = 1.5
        
        # Input tokens are minimal (just the audio metadata)
        input_tokens = 10
        
        # Output tokens based on estimated transcription length
        output_tokens = int((duration_seconds / 60) * words_per_minute * tokens_per_word)
        
        return TokenEstimate(
            input_tokens=input_tokens,
            output_tokens=max(output_tokens, DEFAULT_MAX_TOKENS["STT"]),
        )
    
    def estimate_from_bytes(
        self,
        body: bytes,
        model_type: str = "LLM",
    ) -> TokenEstimate:
        """
        Estimate tokens from raw request body bytes.
        
        Args:
            body: Raw request body bytes
            model_type: Type of model
            
        Returns:
            TokenEstimate
        """
        try:
            request_body = json.loads(body.decode("utf-8"))
            return self.estimate(request_body, model_type)
        except (json.JSONDecodeError, UnicodeDecodeError):
            # If we can't parse, return conservative defaults
            return TokenEstimate(
                input_tokens=100,
                output_tokens=DEFAULT_MAX_TOKENS.get(model_type.upper(), 2048),
            )


# Singleton instance
token_estimation_service = TokenEstimationService()

