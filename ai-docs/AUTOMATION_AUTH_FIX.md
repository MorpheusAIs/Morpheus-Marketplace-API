# Automation Endpoints Authentication Fix

**Date:** 2025-10-24  
**Status:** ✅ Completed  
**Impact:** Backend Breaking Change (Frontend Update Required)

---

## Summary

Changed the authentication method for automation settings endpoints from **API Key** to **JWT Bearer Token** to ensure consistency with other user account management endpoints.

---

## Changes Made

### 🔧 Backend Changes

#### File: `src/api/v1/automation/index.py`

**1. Import Changes:**
```python
# Before
from ....dependencies import get_api_key_user, oauth2_scheme

# After
from ....dependencies import get_current_user
```

**2. GET `/api/v1/automation/settings` Endpoint:**
```python
# Before
async def get_automation_settings(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_api_key_user)  # ❌ API Key Auth
):

# After
async def get_automation_settings(
    current_user: User = Depends(get_current_user),  # ✅ JWT Auth
    db: AsyncSession = Depends(get_db)
):
```

**3. PUT `/api/v1/automation/settings` Endpoint:**
```python
# Before
async def update_automation_settings(
    automation_settings: AutomationSettingsBase,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_api_key_user)  # ❌ API Key Auth
):

# After
async def update_automation_settings(
    automation_settings: AutomationSettingsBase,
    current_user: User = Depends(get_current_user),  # ✅ JWT Auth
    db: AsyncSession = Depends(get_db)
):
```

**4. Updated Docstrings:**

Both endpoints now include clear documentation:
```python
"""
Get/Update automation settings for the authenticated user.

Requires JWT Bearer authentication with Cognito token.
Automation settings control automatic session creation behavior for all of the user's API keys.
"""
```

**5. Variable Name Changes:**

Updated all references from `user.id` to `current_user.id` throughout both endpoints for consistency.

---

## Rationale

### Why This Change Was Necessary

1. **Consistency with User Settings:**
   - Private keys: JWT authentication ✅
   - Delegations: JWT authentication ✅
   - API key management: JWT authentication ✅
   - Automation settings: ~~API key~~ → **JWT authentication** ✅

2. **Logical Ownership:**
   - Automation settings control session behavior **across all of a user's API keys**
   - These are account-level preferences, not API key-specific settings
   - Should be managed at the user account level, just like private keys

3. **Security Model:**
   - API keys should be used for **AI operations** (chat, embeddings, sessions)
   - JWT tokens should be used for **account management** (user settings, keys, preferences)

---

## Breaking Changes

### ⚠️ Frontend Impact

**Before:**
```javascript
// Frontend was using API key
const response = await fetch('/api/v1/automation/settings', {
  headers: {
    'Authorization': `Bearer ${apiKey}`  // ❌ No longer works
  }
});
```

**After:**
```javascript
// Frontend must now use JWT token
const response = await fetch('/api/v1/automation/settings', {
  headers: {
    'Authorization': `Bearer ${jwtToken}`  // ✅ Required
  }
});
```

### Migration Steps for Frontend

1. **Update API calls** to use JWT token instead of API key
2. **Ensure JWT token is available** before accessing automation endpoints
3. **Handle authentication errors** appropriately if JWT is missing/expired
4. **Test thoroughly** to ensure automation settings still work

---

## Testing

### Backend Testing

```bash
# Test GET endpoint with JWT
curl -H "Authorization: Bearer <cognito_jwt_token>" \
     https://api.mor.org/api/v1/automation/settings

# Test PUT endpoint with JWT
curl -X PUT \
     -H "Authorization: Bearer <cognito_jwt_token>" \
     -H "Content-Type: application/json" \
     -d '{"is_enabled": true, "session_duration": 3600}' \
     https://api.mor.org/api/v1/automation/settings

# Verify API key no longer works (should return 401)
curl -H "Authorization: Bearer sk-xxxxx" \
     https://api.mor.org/api/v1/automation/settings
```

**Expected Results:**
- ✅ JWT token works correctly
- ❌ API key returns 401 Unauthorized
- ✅ Response format unchanged (backward compatible data structure)

---

## Swagger Documentation

### Updated Endpoint Documentation

Both automation endpoints now clearly state in their Swagger descriptions:

> **Requires JWT Bearer authentication with Cognito token.**  
> Automation settings control automatic session creation behavior for all of the user's API keys.

### How to Test in Swagger UI

1. Go to `/docs` (Swagger UI)
2. Click "Authorize" button
3. Use **OAuth2** or **BearerAuth** (not APIKeyAuth) to authenticate
4. Test the automation endpoints

---

## Related Documentation

- **Authentication Reference:** `ai-docs/API_AUTH_ENDPOINTS.md` - Complete endpoint authentication catalog
- **Chat Auth Consistency:** `ai-docs/CHAT_STORAGE_AUTH_CONSISTENCY.md` - Similar fix for chat history endpoints

---

## Authentication Summary

### Current Authentication Distribution

| Endpoint Category | Authentication Method |
|------------------|---------------------|
| **User Account** | JWT Bearer Token |
| **API Key Management** | JWT Bearer Token |
| **Private Keys** | JWT Bearer Token |
| **Delegations** | JWT Bearer Token |
| **Automation Settings** | JWT Bearer Token ✅ |
| **Chat Completions** | API Key |
| **Chat History** | API Key |
| **Sessions** | API Key |
| **Embeddings** | API Key |
| **Model Discovery** | Public (No Auth) |

---

## Rollback Instructions

If this change needs to be reverted:

```bash
# Revert the file
git checkout HEAD~1 -- src/api/v1/automation/index.py

# Or manually change back:
# 1. Import get_api_key_user instead of get_current_user
# 2. Change dependencies back to get_api_key_user
# 3. Change current_user back to user
# 4. Update docstrings to remove JWT requirement
```

---

## Deployment Checklist

- [x] Backend code updated
- [x] Docstrings updated
- [x] Authentication reference documentation updated
- [ ] Frontend code updated (pending)
- [ ] Frontend testing completed (pending)
- [ ] Deployment coordinated between backend and frontend (pending)
- [ ] User communication (if needed)

---

## Questions & Answers

**Q: Why not keep API key authentication as an alternative?**  
A: Mixing authentication methods for account settings creates confusion and security concerns. Clear separation is better: JWT for account management, API keys for AI operations.

**Q: Will existing automation settings be lost?**  
A: No. The database schema is unchanged. Only the authentication method changed.

**Q: Can users still have multiple API keys with automation?**  
A: Yes. Automation settings apply to **all** of a user's API keys. That's why they should be managed at the user account level with JWT.

**Q: What happens if a user tries to access with an API key?**  
A: They will receive a 401 Unauthorized error with clear messaging that JWT authentication is required.

---

## Success Criteria

- ✅ Backend endpoints enforce JWT authentication
- ✅ Swagger documentation clearly indicates JWT requirement
- ✅ Endpoint behavior unchanged (except authentication)
- ⚠️ Frontend successfully migrated to use JWT (pending)
- ⚠️ No disruption to user experience (pending testing)

---

**Document Status:** Complete  
**Implementation Status:** Backend ✅ | Frontend ⚠️ Pending

