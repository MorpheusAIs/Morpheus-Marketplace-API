# Proxy Router Bug Fix: Empty Response on Timeout

## Issue

The proxy router returns `HTTP 200 OK` with an empty response body when LLM requests timeout or fail. This is caused by a race condition in the Gin framework where headers are sent before the response is complete.

## Root Cause

**File:** `Morpheus-Lumerin-Node/proxy-router/internal/proxyapi/controller_http.go`  
**Function:** `Prompt()` (lines 181-252)

### Current (Broken) Code

```go
func (c *ProxyController) Prompt(ctx *gin.Context) {
    // ... setup code ...
    
    // Line 219: Set Content-Type header BEFORE making request
    ctx.Writer.Header().Set(constants.HEADER_CONTENT_TYPE, contentType)
    
    // Line 221: Make request with callback that writes directly to ctx.Writer
    err = adapter.Prompt(ctx, &body, func(cbctx context.Context, completion gsc.Chunk, ...) error {
        // ... callback code ...
        
        // Line 236: First Write() sends 200 OK status + headers
        _, err = ctx.Writer.Write(marshalledResponse)
        ctx.Writer.Flush()
        return nil
    })
    
    // Lines 247-250: Try to return error (TOO LATE - headers already sent!)
    if err != nil {
        c.log.Errorf("error sending prompt: %s", err)
        ctx.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})  // ❌ Doesn't work
        return
    }
}
```

### Problem Flow

1. **Headers set early**: Line 219 sets Content-Type before response is ready
2. **First write commits status**: Any Write() to ctx.Writer sends 200 OK automatically
3. **Error handler ineffective**: Can't change HTTP status after first Write()
4. **Timeout scenario**: If adapter times out, callback never runs, but headers are sent
5. **Result**: 200 OK with empty body

## Solution

### Option 1: Buffer Response (Recommended)

Don't write to ctx.Writer until the entire response is ready:

```go
func (c *ProxyController) Prompt(ctx *gin.Context) {
    // ... setup code ...
    
    var responseBuffer bytes.Buffer
    var completionError error
    var contentType string
    
    if body.Stream {
        contentType = constants.CONTENT_TYPE_EVENT_STREAM
    } else {
        contentType = constants.CONTENT_TYPE_JSON
    }
    
    // Collect response in buffer instead of writing directly
    err = adapter.Prompt(ctx, &body, func(cbctx context.Context, completion gsc.Chunk, aiResponseError *gsc.AiEngineErrorResponse) error {
        if aiResponseError != nil {
            // Save error for later
            completionError = fmt.Errorf("AI engine error: %v", aiResponseError)
            return completionError
        }
        
        marshalledResponse, err := json.Marshal(completion.Data())
        if err != nil {
            return err
        }
        
        // Write to buffer instead of ctx.Writer
        if body.Stream {
            responseBuffer.Write([]byte(fmt.Sprintf("data: %s\n\n", marshalledResponse)))
        } else {
            responseBuffer.Write(marshalledResponse)
        }
        
        return nil
    })
    
    // Now check for errors BEFORE committing response
    if err != nil || completionError != nil {
        c.log.Errorf("error sending prompt: %s", err)
        ctx.JSON(http.StatusInternalServerError, gin.H{
            "error": fmt.Sprintf("%v", err),
            "type": "llm_error",
        })
        return
    }
    
    // Only set headers and write response if successful
    ctx.Writer.Header().Set(constants.HEADER_CONTENT_TYPE, contentType)
    ctx.Writer.Write(responseBuffer.Bytes())
    ctx.Writer.Flush()
}
```

### Option 2: Check Before Setting Headers

Check adapter status before setting headers:

```go
func (c *ProxyController) Prompt(ctx *gin.Context) {
    // ... setup code ...
    
    // DON'T set headers yet
    
    // Collect response first
    var responses []gsc.Chunk
    var hasError bool
    
    err = adapter.Prompt(ctx, &body, func(cbctx context.Context, completion gsc.Chunk, aiResponseError *gsc.AiEngineErrorResponse) error {
        if aiResponseError != nil {
            hasError = true
            return fmt.Errorf("AI error: %v", aiResponseError)
        }
        responses = append(responses, completion)
        return nil
    })
    
    // Check for errors BEFORE setting headers
    if err != nil || hasError {
        c.log.Errorf("error sending prompt: %s", err)
        ctx.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
        return
    }
    
    // Now it's safe to set headers and send response
    var contentType string
    if body.Stream {
        contentType = constants.CONTENT_TYPE_EVENT_STREAM
    } else {
        contentType = constants.CONTENT_TYPE_JSON
    }
    ctx.Writer.Header().Set(constants.HEADER_CONTENT_TYPE, contentType)
    
    // Write all responses
    for _, completion := range responses {
        marshalledResponse, _ := json.Marshal(completion.Data())
        if body.Stream {
            ctx.Writer.Write([]byte(fmt.Sprintf("data: %s\n\n", marshalledResponse)))
        } else {
            ctx.Writer.Write(marshalledResponse)
        }
    }
    ctx.Writer.Flush()
}
```

### Option 3: Use Gin Context Status

Explicitly set status code before writing:

```go
func (c *ProxyController) Prompt(ctx *gin.Context) {
    // ... setup code ...
    
    var hasError bool
    var errorMessage string
    
    err = adapter.Prompt(ctx, &body, func(cbctx context.Context, completion gsc.Chunk, aiResponseError *gsc.AiEngineErrorResponse) error {
        if aiResponseError != nil {
            hasError = true
            errorMessage = aiResponseError.Error()
            return nil  // Don't return error, just flag it
        }
        
        // Only write if no error occurred yet
        if !hasError {
            marshalledResponse, err := json.Marshal(completion.Data())
            if err != nil {
                hasError = true
                errorMessage = err.Error()
                return nil
            }
            
            // Write response
            if body.Stream {
                ctx.Writer.Write([]byte(fmt.Sprintf("data: %s\n\n", marshalledResponse)))
            } else {
                ctx.Writer.Write(marshalledResponse)
            }
            ctx.Writer.Flush()
        }
        return nil
    })
    
    // Handle errors after callback completes
    if err != nil || hasError {
        // Only send error if we haven't written anything yet
        if ctx.Writer.Size() == 0 {
            ctx.JSON(http.StatusInternalServerError, gin.H{
                "error": fmt.Sprintf("%v - %s", err, errorMessage),
            })
        } else {
            // Response already started, can only log error
            c.log.Errorf("error after response started: %s - %s", err, errorMessage)
        }
        return
    }
}
```

## Recommended Approach

**Option 1 (Buffer Response)** is recommended for **non-streaming** requests because:
- ✅ Complete error handling
- ✅ Never sends headers prematurely
- ✅ Clean separation of concerns
- ✅ Easy to test

**For streaming requests**, use **Option 3** with size check:
- Streaming requires immediate writes
- Check `ctx.Writer.Size()` to see if response started
- Return proper error only if no bytes written yet

## Testing

### Test Case 1: Timeout

```bash
# Simulate slow LLM that times out
# Proxy should return 500/504, not 200 with empty body

curl -X POST http://localhost:8082/v1/chat/completions?session_id=test123 \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Extremely long prompt..."}],
    "stream": false
  }'

# Expected: HTTP 500/504 with error JSON
# Current (broken): HTTP 200 with empty body
```

### Test Case 2: LLM Error

```bash
# Force LLM error by invalid request
curl -X POST http://localhost:8082/v1/chat/completions?session_id=test123 \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [],
    "stream": false
  }'

# Expected: HTTP 400 with error JSON
# Should work correctly (no timeout involved)
```

### Test Case 3: Network Failure

```bash
# Disconnect provider proxy mid-request
# Expected: HTTP 502 with error JSON
# Current (broken): HTTP 200 with empty body
```

## Impact

**Current Behavior:**
- API Gateway receives 200 OK with empty body
- Causes JSON parsing error in API Gateway
- Returns 500 to user (looks like API Gateway bug)
- Misleading logs show "success" before failure

**After Fix:**
- Proxy router returns proper error status (500/502/504)
- API Gateway receives proper error response
- Returns correct error to user
- Logs show actual failure point

## Related Files

Other endpoints with same pattern:
- `AudioTranscription()` - line 1465
- `AudioSpeech()` - line 1589
- `Embeddings()` - line 1701

All should be reviewed for the same issue.

## References

- Gin Issue: https://github.com/gin-gonic/gin/issues/1804
- Gin Writer: https://github.com/gin-gonic/gin/blob/master/response_writer.go

---

**Priority:** High  
**Impact:** User-facing errors, API reliability  
**Complexity:** Medium (requires careful testing of streaming vs non-streaming)

