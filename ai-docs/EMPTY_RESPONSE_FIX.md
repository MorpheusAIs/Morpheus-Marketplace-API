# Empty Model Response Error Fix

## Problem Identified

The API Gateway was experiencing errors when the proxy router/model provider returned HTTP 200 OK with an **empty response body**. This caused misleading logs and 500 errors to clients.

### Error Sequence (Before Fix)

```json
{"event": "Non-streaming chat completion successful"}  // ❌ Misleading - not validated yet
{"error": "Expecting value: line 1 column 1 (char 0)", "response_text": ""}  // JSON parse fails
POST /api/v1/chat/completions - 500  // ❌ Generic 500 error returned
```

### Root Cause

The code was logging "success" **before** validating that the response was actually valid JSON:

```python
# OLD CODE (BROKEN)
logger.info("Non-streaming chat completion successful")  # ❌ Logs success first
response_content, parse_error = safe_parse_json_response(response, ...)  # Then tries to parse
if response_content:
    return JSONResponse(content=response_content, status_code=200)
else:
    return JSONResponse(status_code=500, content={"error": ...})  # Generic 500
```

## Solution Implemented

### 1. Validate Response Before Logging Success

Changed order of operations to parse and validate **before** logging success:

```python
# NEW CODE (FIXED)
response_content, parse_error = safe_parse_json_response(response, ...)  # ✅ Parse first
if response_content:
    logger.info("Non-streaming chat completion successful")  # ✅ Only log success if valid
    return JSONResponse(content=response_content, status_code=200)
else:
    logger.error("Non-streaming chat completion failed - invalid response format")  # ✅ Log actual error
    return JSONResponse(status_code=502, content={...})  # ✅ Return 502 Bad Gateway
```

### 2. Better Error Classification

**Changed HTTP Status Code:**
- **Before:** `500 Internal Server Error` (implies API Gateway bug)
- **After:** `502 Bad Gateway` (correctly identifies upstream model provider issue)

**Improved Error Messages:**
- Empty responses now clearly identified: `"Empty response body from model provider"`
- User-friendly message: `"The AI model returned an invalid response. This may be due to a model timeout or failure."`
- Includes session_id and error details for debugging

### 3. Enhanced Logging

**For Empty Responses:**
```json
{
  "event": "Model provider returned empty response",
  "error": "Expecting value: line 1 column 1 (char 0)",
  "response_status_code": 200,
  "content_length": 0,
  "event_type": "empty_model_response"
}
```

**For Malformed Responses:**
```json
{
  "event": "Unexpected response format from model provider",
  "error": "Invalid JSON structure",
  "response_text": "<first 500 chars>",  // Truncated to avoid huge logs
  "event_type": "unexpected_response_format"
}
```

## Files Modified

### `src/api/v1/chat/chat_non_streaming.py`

**Changes:**
1. Added missing imports (`Tuple`, `httpx`)
2. Improved `safe_parse_json_response()` function with better error detection
3. Fixed order of operations in main response handler
4. Fixed order of operations in retry response handler
5. Changed 500 errors to 502 errors for upstream issues
6. Added detailed error logging with event types

## Error Flow After Fix

```
1. Proxy router returns 200 OK with empty body
2. safe_parse_json_response() detects empty response
3. Logs: "Model provider returned empty response" (event_type: empty_model_response)
4. Logs: "Non-streaming chat completion failed - invalid response format"
5. Returns 502 Bad Gateway to client with clear error message
```

## Benefits

### For Users
- ✅ **Clear error messages**: "The AI model returned an invalid response"
- ✅ **Correct HTTP status**: 502 (upstream issue) instead of 500 (server bug)
- ✅ **Session ID included**: Users can report issues with specific session IDs

### For Developers
- ✅ **Accurate logs**: No more misleading "success" logs for failures
- ✅ **Better debugging**: Clear event types (`empty_model_response`, `chat_completion_failed`)
- ✅ **Truncated logs**: Large invalid responses don't flood logs (limited to 500 chars)

### For Operations
- ✅ **Correct alerting**: 502 errors indicate upstream model provider issues
- ✅ **Event types**: Easy to filter and aggregate specific error types
- ✅ **Content length tracking**: Can identify if it's truly empty or just whitespace

## Potential Root Causes of Empty Responses

The underlying issue of empty responses from the proxy router may be caused by:

1. **Model Timeout**: Model takes too long to respond, connection closes
2. **Model Failure**: Model crashes or encounters an internal error
3. **Large Context**: Very long prompts may cause issues (DnD narrative in the example was ~500 chars)
4. **Resource Exhaustion**: Model provider out of memory or compute
5. **Network Issues**: Connection drops between proxy router and model provider

## Monitoring Recommendations

### CloudWatch Alerts

1. **Empty Model Responses**
   ```
   Filter: event_type = "empty_model_response"
   Threshold: > 10 occurrences in 5 minutes
   ```

2. **502 Bad Gateway Errors**
   ```
   Metric: HTTP status code 502
   Threshold: > 5% of total requests
   ```

### Log Analysis Queries

```bash
# Count empty response errors by session
grep "empty_model_response" logs | jq -r '.session_id' | sort | uniq -c

# Track which models have empty response issues
grep "empty_model_response" logs | jq -r '.payload.model' | sort | uniq -c

# Analyze request sizes for empty responses
grep "empty_model_response" logs | jq -r '.payload.messages | length'
```

## Testing

### Manual Test Case

```bash
# Simulate empty response from proxy router (requires mock endpoint)
curl -X POST http://localhost:8000/api/v1/chat/completions \
  -H "Authorization: Bearer sk-..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test-model",
    "messages": [{"role": "user", "content": "test"}]
  }'
```

**Expected Response (After Fix):**
```json
{
  "error": {
    "message": "The AI model returned an invalid response. This may be due to a model timeout or failure.",
    "type": "bad_gateway",
    "session_id": "0x...",
    "details": "Invalid response from model provider: Empty response body from model provider"
  }
}
```

**HTTP Status:** `502 Bad Gateway`

## Related Issues

This fix addresses empty response handling but does NOT fix:
- The underlying cause of why proxy router returns empty responses
- Model timeout configuration (may need adjustment in proxy router)
- Session retry logic for empty responses (could be added in future)

## Deployment Notes

- **No database changes required**
- **No configuration changes required**
- **Backward compatible** - only changes error responses
- **Can be deployed independently** - no dependencies on other services

## Future Improvements

1. **Automatic Retry**: Retry empty responses with exponential backoff
2. **Circuit Breaker**: Temporarily disable problematic models after repeated failures
3. **Model Health Tracking**: Track empty response rates per model
4. **Timeout Configuration**: Allow per-model timeout settings
5. **Partial Response Handling**: Handle cases where response is incomplete but not empty

---

**Fixed:** October 14, 2025  
**Issue:** Empty model responses causing misleading logs and incorrect 500 errors  
**Solution:** Validate responses before logging success, return 502 instead of 500, improve error messages

