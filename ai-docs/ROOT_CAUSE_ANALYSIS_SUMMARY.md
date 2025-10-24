# Root Cause Analysis: Empty Response with HTTP 200 OK

## Executive Summary

**Issue:** API Gateway receiving HTTP 200 OK with empty response body from Consumer Proxy-Router  
**Root Cause:** Bug in Morpheus-Lumerin-Node proxy router (Gin framework header commitment issue)  
**Impact:** Users receive 500 errors when LLM requests timeout or fail  
**Fix Location:** `Morpheus-Lumerin-Node/proxy-router/internal/proxyapi/controller_http.go`

---

## Problem Statement

When users make chat completion requests with complex prompts, they sometimes receive errors:

```json
{
  "error": {
    "message": "The AI model returned an invalid response",
    "type": "bad_gateway"
  }
}
```

**Logs showed:**
```
HTTP 200 OK from proxy router
Response body: empty ("")
JSON parse error: "Expecting value: line 1 column 1 (char 0)"
```

---

## Architecture & Request Flow

```
User Request
    ↓
API Gateway (Morpheus-Marketplace-API)
    ↓ POST /v1/chat/completions
Consumer Proxy-Router (routerapi.dev.mor.org:8082)
    ↓
Provider Proxy-Router
    ↓
LLM Server
```

---

## Root Cause Analysis

### Layer 1: API Gateway (Symptom)

**File:** `src/api/v1/chat/chat_non_streaming.py`

**Issue:** Logged "success" before validating response was valid JSON

```python
logger.info("Non-streaming chat completion successful")  # ❌ Premature
response_content, parse_error = safe_parse_json_response(response, ...)  # Then parse
```

**Fix Applied:** ✅ Validate response before logging success, return 502 instead of 500

### Layer 2: Proxy Router Service (Missing Validation)

**File:** `src/services/proxy_router_service.py`

**Issue:** Accepted any HTTP < 400 without validating response body

```python
if response.status_code < 400:
    return response  # ❌ No check if body is empty
```

**Fix Applied:** ✅ Validate response body exists before returning

### Layer 3: Consumer Proxy-Router (Root Cause)

**File:** `Morpheus-Lumerin-Node/proxy-router/internal/proxyapi/controller_http.go`  
**Function:** `Prompt()` lines 181-252

**Issue:** Gin framework sends 200 OK headers before response is complete

```go
// Line 219: Set headers BEFORE request completes
ctx.Writer.Header().Set(constants.HEADER_CONTENT_TYPE, contentType)

// Line 221: Make request
err = adapter.Prompt(ctx, &body, func(...) error {
    // Line 236: First Write() sends "HTTP 200 OK" + headers
    _, err = ctx.Writer.Write(marshalledResponse)
    return nil
})

// Line 247: Try to return error (TOO LATE!)
if err != nil {
    ctx.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})  // ❌ Headers already sent
}
```

**Sequence When LLM Times Out:**
1. Consumer Proxy: Set Content-Type header (line 219)
2. Consumer Proxy → Provider Proxy: Forward request
3. Provider Proxy → LLM Server: Forward request  
4. LLM Server: Processing... (timeout after 180s)
5. Provider Proxy: Return error
6. Consumer Proxy: adapter.Prompt() returns error (line 247)
7. Consumer Proxy: **Try** to return ctx.JSON(500, error) (line 249)
8. **Gin:** Headers already committed! Can't change status!
9. **Result:** Connection closes with 200 OK + empty body

---

## Fix Status

### ✅ Fixed (API Gateway)

1. **`chat_non_streaming.py`** - Validate before logging success
2. **`proxy_router_service.py`** - Validate response body not empty

### 🔧 Needs Fix (Proxy Router)

**File:** `Morpheus-Lumerin-Node/proxy-router/internal/proxyapi/controller_http.go`

**Required Change:** Buffer response before sending headers

See: `ai-docs/PROXY_ROUTER_BUG_FIX_PROPOSAL.md` for detailed fix

---

## Evidence Trail

### 1. API Gateway Logs

```json
{
  "event": "HTTP Request: POST http://routerapi.dev.mor.org:8082/v1/chat/completions",
  "status": "HTTP/1.1 200 OK"
}
{
  "error": "Expecting value: line 1 column 1 (char 0)",
  "response_text": "",
  "response_status_code": 200
}
{
  "event": "POST /api/v1/chat/completions - 500"
}
```

### 2. Proxy Router Code

**controller_http.go:219** - Sets headers early  
**controller_http.go:236** - First write commits status  
**controller_http.go:249** - Error handler ineffective

### 3. Test Scenario

**Request:**
- DnD narrative prompt (~1500 chars)
- Complex generation task
- Takes > 180 seconds (timeout)

**Result:**
- 200 OK with empty body (should be 504 Gateway Timeout)

---

## Why This Happens

### Gin Framework Behavior

In Gin (Go web framework), the `ResponseWriter` automatically sends status code and headers on the **first call to Write()**:

```go
// First Write() triggers:
// 1. HTTP/1.1 200 OK
// 2. Content-Type: application/json
// 3. [headers sent over network]
// 4. Body starts streaming

// Later trying to change status FAILS:
ctx.JSON(500, error)  // ❌ Headers already on wire!
```

### Why Empty Body

If error occurs **before** any Write() call:
1. Headers sent (200 OK)
2. Error occurs
3. Connection closes
4. **Result:** 200 OK with 0 bytes

---

## Impact Assessment

### Before Fix (Current)

| Layer | Behavior | Impact |
|-------|----------|--------|
| LLM Server | Timeout (180s+) | ⚠️ Slow/failing |
| Provider Proxy | Return error | ⚠️ Not shown |
| Consumer Proxy | **Return 200 + empty** | ❌ **Bug here** |
| API Gateway | JSON parse error | ❌ Symptom |
| User | "Internal Server Error" | ❌ Confused |

### After API Gateway Fixes

| Layer | Behavior | Impact |
|-------|----------|--------|
| LLM Server | Timeout (180s+) | ⚠️ Slow/failing |
| Provider Proxy | Return error | ⚠️ Not shown |
| Consumer Proxy | **Return 200 + empty** | ❌ **Bug remains** |
| API Gateway | Detect empty, return 502 | ✅ Better error |
| User | "Bad Gateway" | ✅ Clearer |

### After Full Fix

| Layer | Behavior | Impact |
|-------|----------|--------|
| LLM Server | Timeout (180s+) | ⚠️ Slow/failing |
| Provider Proxy | Return 504 | ✅ Proper error |
| Consumer Proxy | **Return 504 + error JSON** | ✅ **Fixed** |
| API Gateway | Forward 502 | ✅ Correct |
| User | "Gateway Timeout" | ✅ Accurate |

---

## Next Steps

### Immediate (Completed) ✅
1. Deploy API Gateway fixes
   - Better error handling in `chat_non_streaming.py`
   - Response validation in `proxy_router_service.py`
   - Documentation in `EMPTY_RESPONSE_FIX.md`

### Short-term (Required) 🔧
2. Fix Consumer Proxy-Router
   - Implement response buffering in `controller_http.go`
   - See `PROXY_ROUTER_BUG_FIX_PROPOSAL.md`
   - Create PR for Morpheus-Lumerin-Node repo

### Medium-term (Recommended) 📊
3. Investigation & Monitoring
   - Check Consumer Proxy-Router logs for session errors
   - Add CloudWatch alerts for empty responses
   - See `PROXY_ROUTER_EMPTY_RESPONSE_INVESTIGATION.md`

### Long-term (Optimization) 🚀
4. Performance Improvements
   - Investigate LLM server timeout causes
   - Optimize complex prompt handling
   - Add circuit breaker for failing providers

---

## Testing Verification

### Test 1: API Gateway Handles Empty Response ✅

```bash
# Simulate empty response from proxy router
# Expected: 502 Bad Gateway with clear error message
# Status: PASS (after fix)
```

### Test 2: Proxy Router Returns Proper Error ⏳

```bash
# Make request that times out
curl -X POST http://routerapi.dev.mor.org:8082/v1/chat/completions?session_id=test \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "very long prompt..."}]}'

# Expected: HTTP 504 or 500 with error JSON
# Current: HTTP 200 with empty body ❌
# Status: NEEDS FIX
```

---

## Key Learnings

1. **Validate Early:** Always validate upstream responses before logging success
2. **Check Before Commit:** In web frameworks, headers are sent on first write
3. **Buffer When Possible:** For non-streaming, buffer response before committing
4. **Return Correct Status:** 502/504 for upstream issues, not 500
5. **Investigate Layers:** Don't stop at first symptom, trace through full stack

---

## Documentation

- ✅ `EMPTY_RESPONSE_FIX.md` - API Gateway fixes
- ✅ `PROXY_ROUTER_BUG_FIX_PROPOSAL.md` - Detailed fix for proxy router
- ✅ `PROXY_ROUTER_EMPTY_RESPONSE_INVESTIGATION.md` - Investigation steps
- ✅ `ROOT_CAUSE_ANALYSIS_SUMMARY.md` - This document

---

**Analysis Date:** October 14, 2025  
**Analyst:** AI Assistant  
**Status:** API Gateway fixes deployed, Proxy Router fix pending

