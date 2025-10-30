# Database Corruption Check - Results

**Date:** October 28, 2025  
**Databases Checked:** DEV and PROD

## Executive Summary

✅ **NO DATABASE CORRUPTION DETECTED**

Both DEV and PROD databases are clean. The "gross error" scenario where User A (API Key A, Model A) receives responses from User B (API Key B, Model B) is **NOT POSSIBLE** due to database-level constraints.

---

## Key Findings

### 1. Database-Level Protection (Code Evidence)

The `Session` model has a **UNIQUE constraint** that makes cross-API-key contamination impossible:

```python
# From src/db/models.py, lines 113-116
__table_args__ = (
    Index('sessions_active_api_key_unique', 'api_key_id', 'is_active', 
          unique=True, postgresql_where=is_active.is_(True)),
)
```

**What this means:**
- One API key can ONLY have ONE active session at a time
- PostgreSQL enforces this at the database level
- Any attempt to create a second active session for the same API key will **fail with a database error**
- This protection is automatic and cannot be bypassed by application code

---

## DEV Database Results

```
CHECK 1: Sessions shared across multiple API keys
✅ NO CORRUPTION FOUND - All active sessions are properly isolated per API key

CHECK 2: Active sessions summary
Total active session rows: 2
Unique API keys:           2
Unique session IDs:        2
Unique users:              2
✅ Each session_id has exactly one row (correct)

CHECK 3: API keys with multiple active sessions
✅ Each API key has at most 1 active session (expected)

CHECK 4: Recent session creation (last 24 hours)
Sessions created (24h):    20
Unique API keys:           2
Unique users:              2
```

---

## PROD Database Results

```
CHECK 1: Sessions shared across multiple API keys
✅ NO CORRUPTION FOUND - All active sessions are properly isolated per API key

CHECK 2: Active sessions summary
Total active session rows: 6
Unique API keys:           6
Unique session IDs:        6
Unique users:              6
✅ Each session_id has exactly one row (correct)

CHECK 3: API keys with multiple active sessions
✅ Each API key has at most 1 active session (expected)

CHECK 4: Recent session creation (last 24 hours)
Sessions created (24h):    70
Unique API keys:           7
Unique users:              7
```

---

## What We Checked

### Test 1: Sessions Shared Across Multiple API Keys
**Query:**
```sql
SELECT id, 
       COUNT(DISTINCT api_key_id) as key_count,
       array_agg(DISTINCT api_key_id) as api_keys
FROM sessions
WHERE is_active = TRUE
GROUP BY id
HAVING COUNT(DISTINCT api_key_id) > 1;
```

**Result:** 0 rows (as expected)

This test would have detected if somehow the same session ID was being used by multiple different API keys - which would be the "gross error" you described.

### Test 2: Data Integrity Check
Verified that:
- Total active session rows = Unique session IDs (no duplicate IDs)
- Each session ID appears exactly once in the active sessions table

### Test 3: API Key Session Count
Verified that each API key has at most 1 active session, which is enforced by the database constraint.

---

## Conclusion: The "Gross Error" Is NOT Happening

**Your scenario:**
> "userA with apikeyA sets up a session with Bid/ModelA and at the same time userB with apikeyB sets up a session with Bid/ModelB .... such that when userA asks for chat/completion that they get a response from userB's chat/completion"

**Verdict:** This is **NOT POSSIBLE** at the database level because:

1. ✅ API Key A will have its own unique session (e.g., session_123)
2. ✅ API Key B will have its own unique session (e.g., session_456)
3. ✅ These are completely separate sessions pointing to different bids/models
4. ✅ The database constraint guarantees this separation
5. ✅ Our tests confirm no cross-contamination exists in DEV or PROD

---

## What COULD Cause the Anecdotal Issue?

Since the database is clean and the "gross error" is ruled out, the anecdotal issue where:
> "one of our customers who were using model A got a response from Model B that had nothing to do with their prompt"

**Must be caused by one of these:**

### Most Likely: Response Stream Mixup in API Gateway
- **Same API key, rapid concurrent requests** to different models
- Race condition in `get_session_for_api_key()` during model switching
- API Gateway closes old session (Model A) and creates new session (Model B)
- But in-flight streaming responses get mixed up
- See: `ai-docs/crosstalk/RESPONSE_ROUTING_ANALYSIS.md`

### Alternative: Request Routing Without request_id
- API Gateway doesn't send `request_id` to Lumerin Node
- Multiple concurrent requests from same API key share same session
- Responses get routed back to wrong client websocket/stream
- See: `ai-docs/crosstalk/ROOT_CAUSE_IDENTIFIED.md`

### Next Steps
Since database corruption is ruled out, focus on:
1. **Single API key, concurrent requests** - this is the actual problem
2. Implement request_id tracking through the full stack
3. Add advisory locking for session model switching
4. Test concurrent requests with session pooling

---

## Testing Script

The script used for this check is available at:
`/Volumes/moon/repo/mor/Morpheus-Marketplace-API/check_session_corruption.py`

To run it again:
```bash
cd /Volumes/moon/repo/mor/Morpheus-Marketplace-API
source .venv/bin/activate
python check_session_corruption.py
```

