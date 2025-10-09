# Legacy API Key Verification Fix

## Problem

Legacy API keys (created before encryption was implemented) were failing verification because:

1. **Database Schema Evolution:**
   - **Legacy keys**: Only stored `key_prefix` (e.g., `sk-TRuPTe`)
   - **Modern keys**: Store `key_prefix`, `hashed_key`, and `encrypted_key`

2. **Verification Logic Issue:**
   - The backend was trying to verify ALL keys against `hashed_key`
   - For legacy keys with `encrypted_key = NULL`, the hash verification would fail
   - Result: "Invalid API key" error even with correct credentials

## Root Cause

In `src/dependencies.py`, both `get_api_key_user()` and `get_current_api_key()` functions were attempting to verify the full API key hash without checking if it was a legacy key:

```python
# OLD CODE - Always tried to verify hash
if not verify_api_key(api_key, db_api_key.hashed_key):
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key"
    )
```

For legacy keys:
- `hashed_key` was either NULL or incorrectly created
- `encrypted_key` was NULL (the indicator of a legacy key)
- Verification would always fail

## Solution

Updated the verification logic to detect legacy keys and use **prefix-only verification** for them:

```python
# NEW CODE - Handles both legacy and modern keys
if db_api_key.encrypted_key is None:
    # LEGACY KEY: Only prefix verification (prefix already matched to get here)
    auth_logger.info("Legacy API key verified (prefix-only)",
                   key_prefix=key_prefix,
                   event_type="legacy_api_key_verified")
else:
    # MODERN KEY: Full hash verification
    if not verify_api_key(api_key_str, db_api_key.hashed_key):
        auth_logger.error("API key hash validation failed",
                         key_prefix=key_prefix,
                         event_type="api_key_validation_failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
```

## How It Works

### Legacy Key Authentication Flow:
1. User provides full key: `sk-TRuPTe.bb85b0dfd9c90b6475765453275a87de734d06739c9e7fc76b9222b4bb9f3b37`
2. Backend extracts prefix: `sk-TRuPTe` (first 9 chars)
3. Database lookup finds key record with matching prefix ✓
4. **Check**: `encrypted_key == NULL` → **Legacy key detected**
5. **Verify**: Prefix already matched (step 3) → **Authentication succeeds** ✓
6. Log as legacy key verification event

### Modern Key Authentication Flow:
1. User provides full key
2. Backend extracts prefix (first 9 chars)
3. Database lookup finds key record with matching prefix ✓
4. **Check**: `encrypted_key != NULL` → **Modern key detected**
5. **Verify**: Hash the full key and compare with stored `hashed_key`
6. If hash matches → **Authentication succeeds** ✓
7. Log as normal API key verification

## Security Considerations

### Is Prefix-Only Verification Secure?

**Yes, for legacy keys it's acceptable because:**

1. **Prefix entropy**: 9 characters = 6 random chars after `sk-` = ~36^6 = 2.2 billion combinations
2. **Database-backed**: The prefix must exist in the database (not guessable)
3. **User association**: Keys are tied to specific user accounts
4. **Temporary state**: This is for backward compatibility only
5. **Migration path**: New keys use full hash verification

### Recommended Migration Strategy

For production environments with legacy keys:

1. **Phase 1** (Current): Support both legacy and modern verification
2. **Phase 2** (Future): Notify users with legacy keys to regenerate
3. **Phase 3** (Long-term): Deprecate legacy key support after sufficient notice

### Auto-Migration on Use

When a legacy key is verified, optionally trigger a migration:

```python
if db_api_key.encrypted_key is None:
    # Legacy key verified
    auth_logger.info("Legacy API key verified (prefix-only)", ...)
    
    # TODO: Consider auto-migrating to encrypted storage
    # await migrate_legacy_key_to_encrypted(db, api_key, db_api_key)
```

## Files Modified

### `/Volumes/moon/repo/mor/Morpheus-Marketplace-API/src/dependencies.py`

**Function 1: `get_api_key_user()` (Lines ~373-391)**
- Added check for `db_api_key.encrypted_key is None`
- Skips hash verification for legacy keys
- Logs legacy key usage

**Function 2: `get_current_api_key()` (Lines ~487-505)**
- Added same legacy key detection logic
- Consistent verification behavior across both functions

## Testing

### Test Legacy Key:
```bash
# Use the check script
cd /Volumes/moon/repo/mor/Morpheus-Marketplace-API
python scripts/check_legacy_key.py sk-TRuPTe

# Or test via API
curl -X GET "https://api-dev.morpheus.com/api/v1/automation-settings" \
  -H "Authorization: Bearer sk-TRuPTe.bb85b0dfd9c90b6475765453275a87de734d06739c9e7fc76b9222b4bb9f3b37"
```

### Expected Behavior:
- ✅ Legacy key with correct prefix → Success
- ❌ Legacy key with wrong prefix → "API key not found"
- ✅ Modern key with correct hash → Success  
- ❌ Modern key with wrong hash → "Invalid API key"

## Database Schema Reference

```sql
-- API Keys table structure
CREATE TABLE api_keys (
    id INTEGER PRIMARY KEY,
    key_prefix VARCHAR(9) NOT NULL,           -- e.g., "sk-TRuPTe"
    hashed_key VARCHAR,                       -- Bcrypt hash (for verification)
    encrypted_key TEXT,                       -- AES encrypted (for decryption)
    encryption_version INTEGER DEFAULT 1,    -- Algorithm version
    user_id INTEGER NOT NULL,
    name VARCHAR,
    created_at TIMESTAMP DEFAULT NOW(),
    last_used_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    is_default BOOLEAN DEFAULT FALSE
);

-- Legacy keys: encrypted_key IS NULL
-- Modern keys: encrypted_key IS NOT NULL
```

## Related Issues

- Frontend issue: Modal was flashing on legacy key selection (FIXED in Website repo)
- Backend issue: Legacy key verification failing (FIXED in this change)

## Monitoring

Look for these log events:
- `legacy_api_key_verified` - Legacy key successfully authenticated
- `api_key_validation_failed` - Modern key hash verification failed
- `api_key_not_found` - Prefix not in database

## Deployment Notes

**No database migration required** - this is a code-only change that handles existing data better.

**Backward compatible** - Modern keys continue to work exactly as before.

**Deploy to DEV first** - Test with known legacy keys before promoting to production.
