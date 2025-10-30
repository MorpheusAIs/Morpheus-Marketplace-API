# Default Model and Fallback System

This document describes the current default model and fallback logic in the Morpheus Marketplace API, along with planned improvements for model type-aware routing and fallbacks.

## 📋 **Current System Architecture**

### **1. Configuration Level**

**Default Model Setting:**
- **Setting**: `DEFAULT_FALLBACK_MODEL` in `src/core/config.py`
- **Environment Variable**: `DEFAULT_FALLBACK_MODEL` 
- **Current Default**: `"mistral-31-24b"`
- **Scope**: Global fallback for all model types

**Configuration Code:**
```python
# src/core/config.py
DEFAULT_FALLBACK_MODEL: str = Field(default=os.getenv("DEFAULT_FALLBACK_MODEL", "mistral-31-24b"))
```

### **2. Model Resolution Flow**

**Primary Handler:** `ModelRouter.get_target_model()` in `src/core/model_routing.py`

**Resolution Logic:**
```python
async def get_target_model(self, requested_model: Optional[str]) -> str:
    # 1. No model specified → Use default immediately
    if not requested_model:
        return await self._get_default_model_id()
    
    # 2. Try to resolve via DirectModelService
    resolved_id = await direct_model_service.resolve_model_id(requested_model)
    if resolved_id:
        return resolved_id  # ✅ Model found
    else:
        return await self._get_default_model_id()  # ❌ Model not found → fallback
```

### **3. Default Model Resolution Hierarchy**

**Fallback Priority Order:** `_get_default_model_id()`

1. **Configured Default**: If `DEFAULT_FALLBACK_MODEL` exists in active models → Use it
2. **Generic "default"**: If model named "default" exists → Use it  
3. **First Available**: If no defaults found → Use first available model
4. **Error**: If no models at all → Raise `ValueError`

**Implementation:**
```python
async def _get_default_model_id(self) -> str:
    model_mapping = await direct_model_service.get_model_mapping()
    
    # Try configured default (e.g., "mistral-31-24b")
    if DEFAULT_MODEL in model_mapping:
        return model_mapping[DEFAULT_MODEL]
    
    # Try generic "default" model
    if "default" in model_mapping:
        return model_mapping["default"]
    
    # Use first available model
    if model_mapping:
        first_model_name = next(iter(model_mapping.keys()))
        return model_mapping[first_model_name]
    
    # No models available
    raise ValueError("No models available in the system")
```

### **4. Current Usage in Application**

#### **Session Service** (`src/services/session_service.py`)
```python
# Session creation with model resolution
target_model = await model_router.get_target_model(requested_model)
# Creates session with resolved blockchain ID (original or fallback)
```

#### **Chat Completions** (`src/api/v1/chat.py`)
```python
# Extract model from request
requested_model = json_body.pop("model", None)

# Resolve for session comparison
requested_model_id = await model_router.get_target_model(requested_model)

# Compare with existing session model
if session.model != requested_model_id:
    # Switch models or create new session
```

### **5. Current Behavior Examples**

| Request | Available Models | Result | Notes |
|---------|------------------|---------|-------|
| `"venice-uncensored"` | venice-uncensored exists | `0xb603...` | ✅ Direct resolution |
| `"nonexistent-model"` | venice-uncensored exists | `0x8e5c...` (mistral-31-24b) | ❌ Fallback to default |
| `null` or `""` | venice-uncensored exists | `0x8e5c...` (mistral-31-24b) | ⚠️ No model specified |
| `"gpt-4"` | Only TTS models available | `0xae2d...` (whisper-v3) | ⚠️ Type mismatch fallback |

### **6. Logging and Debugging**

**Debug Messages:**
```
[MODEL_DEBUG] Getting target model for requested model: 'nonexistent-model'
[MODEL_DEBUG] Model 'nonexistent-model' not found in active models
[MODEL_DEBUG] Available models: ['venice-uncensored', 'mistral-31-24b', 'whisper-v3']
[MODEL_DEBUG] Using default model: mistral-31-24b
[MODEL_DEBUG] Resolved to default model ID: 0x8e5c0229ab1dac1753b24e6614ae7c4a4896463ca4929d7601b2b241384d3bbd
```

---

## 🚀 **Planned Future Improvements**

### **1. Model Type-Aware Routing**

**Problem:** Current system routes all requests to `/v1/chat/completions` regardless of model type.

**Solution:** Route to appropriate endpoints based on `ModelType`:

| Model Type | Current Endpoint | Future Endpoint | Purpose |
|------------|------------------|-----------------|---------|
| `LLM` | `/v1/chat/completions` | `/v1/chat/completions` | Text generation, chat |
| `EMBEDDING` | `/v1/chat/completions` | `/v1/embeddings` | Text embeddings |
| `TTS` | `/v1/chat/completions` | `/v1/audio/speech` | Text-to-speech |
| `STT` | `/v1/chat/completions` | `/v1/audio/transcriptions` | Speech-to-text |
| `UNKNOWN` | `/v1/chat/completions` | `/v1/chat/completions` | Default behavior |

**Implementation Plan:**
```python
# New endpoint routing logic
async def get_endpoint_for_model_type(model_type: str) -> str:
    endpoint_mapping = {
        "LLM": "/v1/chat/completions",
        "EMBEDDING": "/v1/embeddings", 
        "TTS": "/v1/audio/speech",
        "STT": "/v1/audio/transcriptions",
        "UNKNOWN": "/v1/chat/completions"  # Default
    }
    return endpoint_mapping.get(model_type, "/v1/chat/completions")
```

### **2. Type-Aware Fallback System**

**Problem:** Current fallback ignores model types (e.g., embedding request might fallback to TTS model).

**Solution:** Implement type-specific fallbacks with hierarchy.

#### **Proposed Fallback Configuration:**
```python
# src/core/config.py - Future implementation
DEFAULT_FALLBACK_MODELS: Dict[str, str] = {
    "LLM": "mistral-31-24b",           # Chat/text generation
    "EMBEDDING": "text-embedding-bge-m3", # Text embeddings  
    "TTS": "tts-kokoro",               # Text-to-speech
    "STT": "Whisper-1",                # Speech-to-text
    "UNKNOWN": "mistral-31-24b"        # Generic fallback
}

# Environment variable support
DEFAULT_LLM_MODEL: str = Field(default=os.getenv("DEFAULT_LLM_MODEL", "mistral-31-24b"))
DEFAULT_EMBEDDING_MODEL: str = Field(default=os.getenv("DEFAULT_EMBEDDING_MODEL", "text-embedding-bge-m3"))
DEFAULT_TTS_MODEL: str = Field(default=os.getenv("DEFAULT_TTS_MODEL", "tts-kokoro"))
DEFAULT_STT_MODEL: str = Field(default=os.getenv("DEFAULT_STT_MODEL", "Whisper-1"))
```

#### **Proposed Fallback Logic:**
```python
async def get_target_model_with_type_awareness(
    requested_model: Optional[str], 
    expected_type: Optional[str] = None
) -> Tuple[str, str]:  # Returns (blockchain_id, model_type)
    
    if not requested_model:
        # No model specified - use type-specific default
        if expected_type and expected_type in DEFAULT_FALLBACK_MODELS:
            default_model = DEFAULT_FALLBACK_MODELS[expected_type]
            return await self._resolve_model_with_type(default_model, expected_type)
        else:
            # Fall back to LLM default
            return await self._resolve_model_with_type(DEFAULT_FALLBACK_MODELS["LLM"], "LLM")
    
    # Try to resolve requested model
    resolved_id = await direct_model_service.resolve_model_id(requested_model)
    if resolved_id:
        # Get model type from raw data
        raw_models = await direct_model_service.get_raw_models_data()
        model_data = next((m for m in raw_models if m["Id"] == resolved_id), None)
        model_type = model_data.get("ModelType", "UNKNOWN") if model_data else "UNKNOWN"
        
        # Check type compatibility if expected type is specified
        if expected_type and model_type != expected_type and model_type != "UNKNOWN":
            logger.warning(f"Model type mismatch: requested {requested_model} ({model_type}) for {expected_type} endpoint")
            # Could either warn and continue, or fallback to type-appropriate model
        
        return resolved_id, model_type
    else:
        # Model not found - fallback to type-specific default
        fallback_type = expected_type or "LLM"
        default_model = DEFAULT_FALLBACK_MODELS.get(fallback_type, DEFAULT_FALLBACK_MODELS["LLM"])
        return await self._resolve_model_with_type(default_model, fallback_type)
```

### **3. Enhanced Client Communication**

**Current:** Client never knows if their model was substituted.

**Proposed:** Include fallback information in responses:

```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion",
  "model": "mistral-31-24b",
  "model_info": {
    "requested": "nonexistent-model",
    "resolved": "mistral-31-24b", 
    "fallback_used": true,
    "fallback_reason": "requested_model_not_found"
  }
}
```

### **4. Endpoint-Specific Request Handling**

**Future API Structure:**

#### **Chat Completions** (`/v1/chat/completions`)
- **Accepts:** Any model, but prefers `LLM` type
- **Fallback:** Type-aware fallback to best available LLM
- **Request:** Standard OpenAI chat completion format

#### **Embeddings** (`/v1/embeddings`) 
- **Accepts:** `EMBEDDING` type models only
- **Fallback:** Fallback to best available embedding model
- **Request:** OpenAI embeddings format
```json
{
  "model": "text-embedding-bge-m3",
  "input": "Text to embed"
}
```

#### **Audio Speech** (`/v1/audio/speech`)
- **Accepts:** `TTS` type models only  
- **Fallback:** Fallback to best available TTS model
- **Request:** OpenAI TTS format
```json
{
  "model": "tts-kokoro",
  "input": "Text to speak",
  "voice": "alloy"
}
```

#### **Audio Transcriptions** (`/v1/audio/transcriptions`)
- **Accepts:** `STT` type models only
- **Fallback:** Fallback to best available STT model  
- **Request:** OpenAI transcription format (multipart/form-data)

---

## 🔧 **Implementation Plan**

### **Phase 1: Type-Aware Fallbacks**
1. ✅ Add `ModelType` field to API responses (completed)
2. ⏳ Implement type-specific default configuration
3. ⏳ Update `ModelRouter` with type-awareness
4. ⏳ Add fallback logging and client notification

### **Phase 2: Endpoint Routing**  
1. ⏳ Implement endpoint detection based on model type
2. ⏳ Create new API endpoints (`/embeddings`, `/audio/speech`, `/audio/transcriptions`)
3. ⏳ Update proxy router integration
4. ⏳ Add request format validation per endpoint

### **Phase 3: Enhanced Features**
1. ⏳ Model compatibility validation  
2. ⏳ Performance-based fallback selection
3. ⏳ User preferences for fallback behavior
4. ⏳ Analytics and monitoring for fallback usage

---

## 📊 **Current Model Type Distribution**

Based on recent `active_models.json` data:

| Model Type | Count | Example Models |
|------------|-------|----------------|
| `UNKNOWN` | ~12 | venice-uncensored, mistral-31-24b, llama-3.3-70b |
| `LLM` | ~1 | LMR-Hermes-3-Llama-3.1-8B |
| `EMBEDDING` | ~1 | text-embedding-bge-m3 |
| `TTS` | ~2 | whisper-v3, tts-kokoro |
| `STT` | ~1 | Whisper-1 |

**Note:** Many models are currently marked as `UNKNOWN` and may need reclassification.

---

## 🚨 **Considerations and Risks**

### **Backward Compatibility**
- Current clients expect all requests to work with `/chat/completions`
- Need migration strategy for existing integrations
- Consider deprecation timeline for old behavior

### **Model Classification**
- Many existing models are `UNKNOWN` type
- Need strategy for reclassifying existing models
- Handle models that support multiple capabilities

### **Fallback Behavior**
- Should embedding requests to `/chat/completions` be redirected or rejected?
- How to handle model type mismatches gracefully
- Performance impact of additional type checking

### **Configuration Management**
- Environment-specific defaults (dev vs prod)
- User/organization-specific fallback preferences
- Dynamic fallback configuration updates

---

## 📚 **References**

- **Current Implementation**: `src/core/model_routing.py`
- **Configuration**: `src/core/config.py`
- **Usage**: `src/services/session_service.py`, `src/api/v1/chat.py`
- **Model Data**: DirectModelService in `src/core/direct_model_service.py`
- **OpenAI API Compatibility**: [OpenAI API Reference](https://platform.openai.com/docs/api-reference)

---

*Last Updated: September 15, 2025*  
*Status: Current system documented, future improvements planned*
