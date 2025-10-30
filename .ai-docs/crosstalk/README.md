# Session Crosstalk Analysis

## Quick Start

**Problem:** Concurrent requests using the same API key can get mixed responses.

**Status:** Root cause identified, database corruption ruled out.

---

## Documentation

### 📋 [PROBLEM_AND_SOLUTION.md](./PROBLEM_AND_SOLUTION.md) ⭐ **START HERE**

Complete analysis including:
- What's happening and why
- Confirmed vs ruled-out scenarios
- Step-by-step action plan
- Code locations and fixes

### ✅ [DATABASE_CORRUPTION_CHECK.md](./DATABASE_CORRUPTION_CHECK.md)

Database audit results proving no cross-API-key contamination.

---

## Test Scripts

### Test Concurrent Requests

```bash
python ai-docs/crosstalk/test_session_crosstalk.py \
    --api-key YOUR_API_KEY \
    --base-url https://api.mor.org
```

### Test Team Usage (Shared API Key)

```bash
python ai-docs/crosstalk/test_team_api_key_usage.py \
    --api-key SHARED_KEY \
    --team-size 5
```

### Test Response Mixup Detection

```bash
python ai-docs/crosstalk/test_response_mixup.py \
    --api-key1 KEY1 \
    --api-key2 KEY2
```

---

## The Issue in 30 Seconds

```
Same API key → Same session_id → Multiple concurrent requests

Request A: "Tell me about dogs"     ┐
                                    ├→ Both use session_id: 0xa505...
Request B: "Explain quantum"        ┘

Provider can't differentiate → Response mixup possible
```

**Root causes:**
1. ❌ No `request_id` sent to provider
2. ❌ No `model_id` sent to provider  
3. ⚠️ One API key = one active session (by design)

**Fixes:**
1. Add `request_id` end-to-end tracking
2. Send `model_id` to provider
3. Add advisory locking for session operations
4. (Future) Implement session pooling

---

## Priority Actions

### Critical (This Week)
- [ ] Add `request_id` to provider communication
- [ ] Add `model_id` to provider headers
- [ ] Run test scripts to verify fix

### Important (Next 2 Weeks)
- [ ] Implement advisory locking
- [ ] Add monitoring for concurrent sessions

### Future (Next Month)
- [ ] Design session pooling architecture
- [ ] Remove single-session-per-key limitation

---

## Risk Level

| Scenario | Risk | Status |
|----------|------|--------|
| Same API key, concurrent requests | 🚨 HIGH | Fix in progress |
| Model switching race condition | ⚠️ MEDIUM | Mitigated by locking |
| Different API keys getting mixed | ✅ NONE | Ruled out |

---

**Last Updated:** October 28, 2025  
**Docs:** 2 core files + 3 test scripts  
**Next:** Implement critical fixes
