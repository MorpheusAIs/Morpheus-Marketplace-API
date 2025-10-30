# Session Crosstalk - Problem Analysis & Solutions

**Last Updated:** October 28, 2025  
**Status:** Root causes identified, solutions documented

---

## Executive Summary

**Problem:** Concurrent requests using the same API key can experience response mixing (crosstalk) where User A receives responses intended for User B.

**Root Cause:** The API Gateway enforces "one API key = one active session" at the database level, causing multiple concurrent requests to share the same `session_id` without unique request tracking to the provider.

**Impact:** HIGH - Potential data leak, privacy violation, incorrect responses

**Status:** Database corruption ruled out ✅ | Architecture issue confirmed ❌

---

## What We Know For Certain

### ✅ Confirmed Facts

1. **Database-level constraint exists:**
   ```python
   # src/db/models.py lines 113-116
   __table_args__ = (
       Index('sessions_active_api_key_unique', 'api_key_id', 'is_active', 
             unique=True, postgresql_where=is_active.is_(True)),
   )
   ```
   - PostgreSQL enforces: **1 API key = 1 active session**
   - Different API keys CANNOT share sessions
   - Database corruption check: ✅ CLEAN (both DEV and PROD)

2. **request_id is NOT sent to provider:**
   ```python
   # src/services/proxy_router_service.py:616-622
   headers = {
       "session_id": session_id,
       "X-Session-ID": session_id,
   }
   # ❌ NO request_id header sent!
   ```
   - Generated for logging only
   - Never forwarded to Lumerin Node
   - Provider has no way to differentiate concurrent requests

3. **model_id is NOT sent to provider:**
   ```python
   # src/services/proxy_router_service.py:616-622
   # ❌ NO model_id header sent!
   ```
   - Provider expects it: `type PromptHead struct { ModelID lib.Hash \`header:"model_id"\` }`
   - Could cause wrong model to process request

4. **Concurrent requests verified in logs:**
   ```
   16:59:50.246 - request cee01be1, session 0xa505...
   16:59:50.902 - request 96044b68, session 0xa505... (656ms overlap!)
   16:59:52.051 - request df8b0551, session 0xa505...
   ```
   - Multiple active requests
   - Same session ID
   - Provider failures: "connection closed without sending data"

---

## Problem Scenarios

### Scenario 1: Same API Key, Concurrent Requests (CONFIRMED)

**Trigger:** Multiple developers sharing one API key, or single user making rapid requests

```
Time 0ms:   User A (API Key X) → Request 1 → session_123 → "Tell me about dogs"
Time 50ms:  User B (API Key X) → Request 2 → session_123 → "Explain quantum physics"

Both requests:
  ✅ Same API key
  ✅ Same session_id (0xa505...)
  ❌ Different request_ids (but not sent to provider)
  ❌ Provider can't differentiate them
```

**Result:** Responses may be mixed - User A might get quantum physics response

**Likelihood:** HIGH if team shares API keys

---

### Scenario 2: Same API Key, Model Switching (CONFIRMED)

**Trigger:** Rapidly switching between models with same API key

```
Request 1: API Key X, model "qwen3-235b"    → Creates session_ABC
Request 2: API Key X, model "llama-70b"     → Closes session_ABC, creates session_DEF
Request 3: API Key X, model "qwen3-235b"    → Closes session_DEF, creates session_GHI
```

**Code:**
```python
# src/services/session_service.py:70-83
if session.model == requested_model_id:
    return session  # Reuse
else:
    await close_session(db, session.id)  # Close old
    return await create_automated_session(...)  # Create new
```

**Race Condition:**
- No locking during model switch
- Old session responses could arrive after switch
- New session created before old fully closed

**Result:** Response from old model/session delivered to new request

**Likelihood:** MEDIUM during rapid model testing

---

### Scenario 3: Different API Keys, Response Mixup (RULED OUT)

**Original concern:** User A (API Key A, Model A) gets User B (API Key B, Model B) response

**Database check:**
```sql
-- Looking for: same session shared by multiple API keys
SELECT id, COUNT(DISTINCT api_key_id) as key_count
FROM sessions
WHERE is_active = TRUE
GROUP BY id
HAVING COUNT(DISTINCT api_key_id) > 1;

-- Result: 0 rows (impossible due to UNIQUE constraint)
```

**Verdict:** ✅ **RULED OUT** - Different API keys cannot share sessions

**Alternative explanation:** If this happened, it was actually Scenario 1 (same API key, shared by team)

---

## Architecture Flow

```
┌──────────────────────────────────────────────────────┐
│ Client Request                                       │
│   API Key: xyz123                                    │
│   Model: qwen3-235b                                  │
│   Prompt: "Tell me about dogs"                       │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ API Gateway (src/api/v1/chat/index.py)              │
│   1. Validates API key                               │
│   2. Generates request_id: "abc123" (logging only)   │
│   3. Calls get_session_for_api_key()                 │
│      → Returns session_id: "0xa505..."               │
│   4. Forwards to provider with ONLY session_id       │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ Consumer Node (Morpheus-Lumerin-Node)                │
│   - Receives: session_id = 0xa505...                 │
│   - GetAdapter(sessionID) → RemoteModel              │
│   - Forwards to provider via RPC/TCP                 │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ Provider Node                                        │
│   - Validates session 0xa505...                      │
│   - Routes to LLM based on session.modelID           │
│   - Returns response on... which connection?         │
│   - ❌ No request_id to correlate!                   │
└──────────────────────────────────────────────────────┘
```

**Problem:** When 2 requests share session `0xa505...`, provider can't tell them apart!

---

## Code Locations

### Critical Files

1. **`src/db/models.py`** (lines 97-116)
   - Session model definition
   - UNIQUE constraint on (api_key_id, is_active)

2. **`src/services/session_service.py`** (lines 52-86)
   - `get_session_for_api_key()` - returns single active session
   - Model switching logic (closes old, creates new)

3. **`src/crud/session.py`** (lines 8-26)
   - `get_active_session_by_api_key()` - query returns ONE session
   
4. **`src/api/v1/chat/index.py`** (lines 73, 149-185)
   - Generates `request_id` (line 73)
   - Calls session service (lines 164-174)

5. **`src/services/proxy_router_service.py`** (lines 584-649)
   - `chatCompletions()` - sends to provider
   - ❌ Missing: `request_id` in headers
   - ❌ Missing: `model_id` in headers

6. **`proxy-router/internal/proxyapi/controller_http.go`** (lines 181-252)
   - Provider expects: `PromptHead{ModelID, SessionID, ChatID}`
   - Only receives: `SessionID`

---

## Solutions

### Solution 1: Add Request ID Tracking (CRITICAL - Do First)

**Why:** Prevents response mixup by uniquely identifying each request end-to-end

**Changes:**

#### A. API Gateway sends request_id

```python
# src/services/proxy_router_service.py
async def chatCompletions(
    *,
    session_id: str,
    messages: list,
    request_id: str = None,  # ← ADD
    **kwargs
) -> httpx.Response:
    
    if not request_id:
        request_id = str(uuid.uuid4())
    
    headers = {
        "Content-Type": "application/json",
        "session_id": session_id,
        "X-Session-ID": session_id,
        "X-Request-ID": request_id,  # ← ADD
    }
    
    payload = {
        "messages": messages,
        "request_id": request_id,  # ← ADD (for provider to echo)
        **kwargs
    }
```

#### B. Update callers to pass request_id

```python
# src/api/v1/chat/chat_non_streaming.py
response = await proxy_router_service.chatCompletions(
    session_id=session_id,
    messages=messages,
    request_id=request_id,  # ← ADD
    **chat_params
)

# src/api/v1/chat/chat_streaming.py
async with proxy_router_service.chatCompletionsStream(
    session_id=session_id,
    messages=messages,
    request_id=request_id,  # ← ADD (use stream_trace_id)
    **chat_params
) as response:
```

#### C. Provider must echo request_id

**Lumerin Node changes needed:**
- Accept `X-Request-ID` header
- Include `request_id` in response body
- Use for logging/correlation

#### D. Validate response matches request

```python
# src/api/v1/chat/chat_non_streaming.py
response_data = response.json()
if response_data.get("request_id") != request_id:
    logger.critical("Response mixup detected!",
                   expected=request_id,
                   received=response_data.get("request_id"))
    # Retry or error
```

**Impact:** Prevents wrong response from being delivered

---

### Solution 2: Send model_id to Provider (CRITICAL - Do First)

**Why:** Provider expects `model_id` in header but doesn't receive it

**Changes:**

```python
# src/services/proxy_router_service.py
async def chatCompletions(
    *,
    session_id: str,
    messages: list,
    model_id: str = None,  # ← ADD (blockchain model ID)
    **kwargs
):
    headers = {
        "session_id": session_id,
        "model_id": model_id,  # ← ADD
    }
```

**Challenge:** Need blockchain model ID (Hash), not friendly name

```python
# In src/api/v1/chat/index.py before calling proxy
from src.crud import model as model_crud

# Get blockchain model ID
model_record = await model_crud.get_model_by_name(db, requested_model)
blockchain_model_id = model_record.blockchain_id  # or whatever field stores it

# Pass to proxy
response = await proxy_router_service.chatCompletions(
    session_id=session_id,
    model_id=blockchain_model_id,  # ← ADD
    **params
)
```

---

### Solution 3: Add Advisory Locking (SHORT-TERM)

**Why:** Prevents concurrent session operations during model switching

**Changes:**

```python
# src/services/session_service.py
from sqlalchemy import select
from src.db.models import APIKey

async def get_session_for_api_key(
    db: AsyncSession,
    api_key_id: int,
    user_id: int,
    requested_model: Optional[str] = None,
    session_duration: Optional[int] = None,
    model_type: Optional[str] = "LLM"
) -> Optional[Session]:
    
    # Acquire row-level lock on API key
    await db.execute(
        select(APIKey)
        .where(APIKey.id == api_key_id)
        .with_for_update()  # ← ADD: locks this API key row
    )
    
    # Rest of existing logic...
    session = await session_crud.get_active_session_by_api_key(db, api_key_id)
    # ...
```

**Impact:**
- ✅ Serializes session operations per API key
- ✅ Prevents race conditions during model switch
- ❌ May slow down concurrent requests from same API key (but they'd fail anyway)

---

### Solution 4: Session Pooling (LONG-TERM)

**Why:** Allows true concurrent request handling per API key

**Concept:** Allow multiple active sessions per (API key, model) pair

```python
# src/crud/session.py
async def get_available_session_for_model(
    db: AsyncSession, 
    api_key_id: int,
    model_id: str,
    max_sessions_per_key: int = 5
) -> Optional[Session]:
    """Get or create available session for this API key + model"""
    
    # Find active sessions for this API key + model
    result = await db.execute(
        select(Session)
        .where(
            Session.api_key_id == api_key_id,
            Session.model == model_id,
            Session.is_active == True
        )
        .order_by(Session.created_at.desc())
    )
    sessions = result.scalars().all()
    
    # TODO: Check each session's capacity/availability
    # For now, create new if under limit
    if len(sessions) < max_sessions_per_key:
        return None  # Signal to create new
    else:
        return sessions[0]  # Return most recent
```

**Requirements:**
- Remove UNIQUE constraint on (api_key_id, is_active)
- Add session capacity tracking
- Add session selection logic
- Test blockchain billing (multiple sessions per key)

---

## Action Plan

### Phase 1: Critical Fixes (This Week)

**Step 1:** Add request_id tracking
- [ ] Modify `proxy_router_service.py` to send `X-Request-ID`
- [ ] Update callers to pass `request_id`
- [ ] Coordinate with Lumerin Node team to accept/echo `request_id`
- [ ] Add response validation

**Step 2:** Add model_id to provider requests
- [ ] Find where blockchain model ID is stored
- [ ] Modify `proxy_router_service.py` to send `model_id` header
- [ ] Update callers to pass blockchain model ID
- [ ] Test with Lumerin Node

**Step 3:** Test concurrent requests
- [ ] Run `test_session_crosstalk.py`
- [ ] Run `test_team_api_key_usage.py`
- [ ] Verify no response mixup

### Phase 2: Stability Improvements (Next 2 Weeks)

**Step 4:** Add advisory locking
- [ ] Implement `with_for_update()` in session lookup
- [ ] Test performance impact
- [ ] Monitor concurrent request behavior

**Step 5:** Enhanced monitoring
- [ ] Add metrics for concurrent sessions
- [ ] Alert on response validation failures
- [ ] Track request_id through logs

### Phase 3: Architecture Enhancement (Next Month)

**Step 6:** Design session pooling
- [ ] Define session capacity model
- [ ] Design session selection algorithm
- [ ] Plan database migration (remove UNIQUE constraint)
- [ ] Verify blockchain billing compatibility

**Step 7:** Implement session pooling
- [ ] Update session CRUD operations
- [ ] Modify session service
- [ ] Add capacity tracking
- [ ] Load test

---

## Testing

### Test 1: Concurrent Request Mixup

```bash
cd /Volumes/moon/repo/mor/Morpheus-Marketplace-API

python ai-docs/crosstalk/test_session_crosstalk.py \
    --api-key YOUR_API_KEY \
    --base-url https://api.mor.org
```

**Expected:** No response mixing, all requests get correct responses

### Test 2: Team API Key Usage

```bash
python ai-docs/crosstalk/test_team_api_key_usage.py \
    --api-key SHARED_TEAM_KEY \
    --team-size 5 \
    --base-url https://api.mor.org
```

**Expected:** Each team member gets their own correct response

### Test 3: Model Switching

```python
# Rapid model switching test
import asyncio, httpx

async def test():
    for i in range(10):
        model = "qwen3-235b" if i % 2 == 0 else "llama-70b"
        response = await client.post(
            "https://api.mor.org/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"model": model, "messages": [{"role": "user", "content": f"Say: {i}"}]}
        )
        print(f"Request {i} ({model}): {response.json()}")
```

**Expected:** Clean transitions, correct model responses

---

## Risk Assessment

| Issue | Risk Level | Fixed By |
|-------|-----------|----------|
| No request_id tracking | 🚨 CRITICAL | Solution 1 |
| No model_id sent | 🚨 CRITICAL | Solution 2 |
| Concurrent same API key | ⚠️ HIGH | Solution 1 + 4 |
| Model switch race condition | ⚠️ MEDIUM | Solution 3 |
| Database corruption | ✅ NONE | Already ruled out |

---

## Key Takeaways

1. ✅ **Database is clean** - no corruption, UNIQUE constraint working
2. ❌ **request_id not sent to provider** - critical missing feature
3. ❌ **model_id not sent to provider** - could cause wrong model usage
4. ⚠️ **Same API key = same session** - architectural limitation
5. 🎯 **Focus on Solutions 1 & 2 first** - prevent data leaks
6. 🔮 **Session pooling is future** - enables true concurrency

---

**Priority:** 🚨 P0 - Data leak potential  
**Timeline:** Critical fixes this week, pooling next month  
**Owner:** Engineering team + Lumerin Node team (for provider changes)

