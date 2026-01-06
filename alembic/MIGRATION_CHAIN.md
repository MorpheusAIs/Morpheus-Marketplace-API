# Alembic Migration Chain

## How Alembic Determines Execution Order

**⚠️ IMPORTANT:** Alembic does **NOT** use filenames to determine order!

The execution order is determined by the `revision` and `down_revision` fields inside each migration file. Alembic builds a dependency graph and follows the chain.

## Current Migration Chain (in execution order)

```
1. d4ae65008d6d (create_initial_tables)
   ↓
2. 69491a79cfd0 (add_is_active_to_user_model)
   ↓
3. 3ec3925c8904 (add_name_field_to_apikey_model)
   ↓
4. 7c29c35fc9bc (add_user_sessions_table)
   ↓
5. fix_session_constraints
   ↓
6. d00825f2a89a (add_delegation_table)
   ↓
7. 881e615d25ac (consolidate_session_model)
   ↓
8. 5f7a3e1b8d42 (add_updated_at_to_users)
   ↓
9. 6f8a4e1b9d43 (replace_local_auth_with_cognito)
   ↓
10. add_chat_tables (2025_01_22_1200)
    ↓
11. add_message_role_enum (2025_09_15_1740)
    ↓
12. add_is_default_api_keys (2025_09_22_1109) ✅ Now has date prefix
    ↓
13. add_encrypted_api_keys (2025_09_23_0855) ✅ Now has date prefix
    ↓
14. fix_enum_name_2025 (2025_10_06_1500) - fixes enum mismatch
    ↓
15. social_login_prep_2025 (2025_12_02_1400) ← NEW - prepares for social login
```

## All Files Now Have Date Prefixes ✅

All migration files now follow the standard naming convention for better human readability:

### 1. `2025_09_22_1109_add_is_default_to_api_keys.py`
- **Revision:** `add_is_default_api_keys`
- **Down Revision:** `add_message_role_enum`
- **Position:** #12 in the chain
- **Purpose:** Adds `is_default` column to API keys table
- **Created:** 2025-09-22 at 11:09

### 2. `2025_09_23_0855_add_encrypted_api_keys.py`
- **Revision:** `add_encrypted_api_keys`
- **Down Revision:** `add_is_default_api_keys`
- **Position:** #13 in the chain (CURRENT HEAD in dev DB)
- **Purpose:** Adds encrypted API key storage
- **Created:** 2025-09-23 at 08:55

## Key Points

1. **Filename doesn't matter** - Only the `revision` and `down_revision` fields matter
2. **All `.py` files in `/alembic/versions/` are discovered** by Alembic
3. **Alembic follows the chain** from your current version to the latest HEAD
4. **Date prefixes are just for human readability** - they make it easier to understand when migrations were created

## Best Practice

**✅ Recommended naming convention:**
```
YYYY_MM_DD_HHMM_<revision_id>_<description>.py
```

**Example:**
```
2025_10_06_1500_fix_enum_name_2025_fix_message_role_enum_name.py
```

**⚠️ But it's not required!** The two files without dates will work fine.

## Verifying Migration Order

To see the current migration chain in your database:

```bash
# Check current version
alembic current

# Show migration history
alembic history

# Show specific migration details
alembic show <revision_id>
```

## For Dev Database

Current state:
```
Current version: fix_enum_name_2025
Next migration:  social_login_prep_2025
```

When you deploy, Alembic will automatically:
1. See current version is `fix_enum_name_2025`
2. Find the next migration in the chain: `social_login_prep_2025`
3. Run that migration
4. Update `alembic_version` table to `social_login_prep_2025`

## Latest Migration

**social_login_prep_2025** (2025-12-02 14:00:00)
- Makes email column nullable (some auth methods don't provide email)
- Removes UNIQUE constraint on email (same email can exist across different providers)
- Keeps non-unique index on email for performance
- Makes name column nullable for consistency
- Prepares database for social login (Google, Facebook, GitHub) and alternative auth (magic link, phone)
- cognito_user_id remains the ONLY unique identifier

## Summary

✅ **All migration files now have proper date prefixes** for better human readability.

✅ Files renamed:
  - `add_is_default_api_keys.py` → `2025_09_22_1109_add_is_default_to_api_keys.py`
  - `add_encrypted_api_keys.py` → `2025_09_23_0855_add_encrypted_api_keys.py`

✅ **No functional changes** - only filenames updated for consistency.

✅ Alembic continues to follow the `revision` and `down_revision` chain (not filenames).

✅ The migration chain is valid and ready for deployment!

