# Message Role Enum Type Mismatch Fix

## Problem Identified

The dev database has an enum type name mismatch causing chat message insertion to fail.

### Error Message
```
(sqlalchemy.dialects.postgresql.asyncpg.ProgrammingError) <class 'asyncpg.exceptions.DatatypeMismatchError'>: 
column "role" is of type messagerole but expression is of type message_role
HINT: You will need to rewrite or cast the expression.
```

### Root Cause

**Database State Investigation (dev database: `db.dev.mor.org`):**

```sql
-- Check existing enum types
SELECT typname, typtype FROM pg_type WHERE typname IN ('messagerole', 'message_role');
```

**Result:**
- ❌ `messagerole` exists (without underscore)
- ✅ `message_role` exists (with underscore)

```sql
-- Check which enum the messages table uses
SELECT column_name, data_type, udt_name FROM information_schema.columns 
WHERE table_name = 'messages' AND column_name = 'role';
```

**Result:**
- ❌ `messages.role` column uses `messagerole` (wrong)
- ✅ SQLAlchemy code expects `message_role` (correct)

**Current Migration Version:**
```sql
SELECT version_num FROM alembic_version;
-- Result: add_encrypted_api_keys
```

### Why This Happened

The database has **both** enum types but the `messages.role` column is pointing to the wrong one (`messagerole` without underscore). This likely occurred due to:

1. An older migration created `messagerole` (no underscore)
2. A newer migration created `message_role` (with underscore) 
3. The `messages` table was never migrated to use the correct enum
4. The SQLAlchemy model expects `message_role`

### Why Local Testing Works

Local testing with `./scripts/docker-test.sh` works because:
- Fresh database created each time
- All migrations run in order from scratch
- Results in correct `message_role` enum being used

## Solution

Created migration: `2025_10_06_1500_fix_message_role_enum_name.py`

**Migration Chain:**
```
add_encrypted_api_keys (current)
    ↓
fix_enum_name_2025 (new) ← Fixes the enum issue
```

### Migration Logic

The migration handles all possible scenarios:

1. **Both enums exist** (current dev state):
   - Migrate `messages.role` column to use `message_role`
   - Drop the old `messagerole` enum
   
2. **Only messagerole exists**:
   - Rename `messagerole` to `message_role`
   
3. **Neither exists**:
   - Create `message_role` enum
   
4. **Only message_role exists** (local test state):
   - Do nothing (already correct)

### Migration SQL

```sql
DO $$
BEGIN
    -- Check if the wrong enum name exists
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'messagerole') THEN
        -- Check if the correct name doesn't already exist
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'message_role') THEN
            -- Rename the enum type
            ALTER TYPE messagerole RENAME TO message_role;
            RAISE NOTICE 'Renamed enum type from messagerole to message_role';
        ELSE
            -- Both exist - need to migrate the column to use the correct one
            -- First, alter the column to use the correct enum
            ALTER TABLE messages ALTER COLUMN role TYPE message_role USING role::text::message_role;
            -- Drop the old enum type
            DROP TYPE messagerole;
            RAISE NOTICE 'Migrated column to message_role and dropped messagerole';
        END IF;
    ELSIF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'message_role') THEN
        -- Neither exists - create the correct one
        CREATE TYPE message_role AS ENUM ('user', 'assistant');
        RAISE NOTICE 'Created message_role enum type';
    ELSE
        -- Correct enum already exists
        RAISE NOTICE 'Enum type message_role already exists correctly';
    END IF;
END $$;
```

## Deployment Steps

### For Dev Environment

1. **Push the migration to the repository**
   ```bash
   git add alembic/versions/2025_10_06_1500_fix_message_role_enum_name.py
   git commit -m "Fix message_role enum type mismatch in dev database"
   git push origin dev
   ```

2. **CI/CD will automatically:**
   - Run the migration during deployment
   - Fix the enum type issue
   - Chat history endpoints will work correctly

### For Production Environment

The same migration will:
- Check production database state
- Apply the appropriate fix based on what exists
- Ensure consistent enum naming

## Verification

After deployment, verify the fix:

```bash
# Check enum types
psql "postgresql://morpheus:PASSWORD@db.dev.mor.org:5432/morpheusapi" \
  -c "SELECT typname FROM pg_type WHERE typname IN ('messagerole', 'message_role');"

# Expected result: Only 'message_role' should exist

# Check messages.role column
psql "postgresql://morpheus:PASSWORD@db.dev.mor.org:5432/morpheusapi" \
  -c "SELECT udt_name FROM information_schema.columns WHERE table_name = 'messages' AND column_name = 'role';"

# Expected result: message_role
```

**Test chat message creation:**
```bash
curl -X POST https://api.dev.mor.org/api/v1/chat-history/chats/{chat_id}/messages \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"role": "user", "content": "Test message"}'

# Should succeed without enum type errors
```

## Summary

✅ **Issue:** Dev database has `messages.role` using wrong enum type (`messagerole`)  
✅ **Cause:** Database has both enum types, column uses the wrong one  
✅ **Solution:** Migration migrates column to correct enum and removes old one  
✅ **Impact:** Chat message creation will work in all environments  
✅ **Safety:** Migration handles all possible database states gracefully  

The migration is idempotent and safe to run multiple times.

