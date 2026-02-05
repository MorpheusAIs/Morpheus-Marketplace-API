# Social Login Support - Database Migration Summary

**Date:** December 2, 2025  
**Migration:** `social_login_prep_2025`  
**Purpose:** Prepare system for social login, magic link, and alternative authentication methods

## Problem Statement

The original database schema enforced:
- UNIQUE constraint on `email` column
- NOT NULL constraint on `email` column

This caused issues when:
1. Same email used across different identity providers (Google, Facebook, GitHub)
2. Social login where user denies email permission
3. Magic link/passwordless authentication without email collection
4. Phone number authentication (no email)

## Solution Overview

**Core Principle:** `cognito_user_id` is the ONLY unique identifier. Email is optional metadata.

### Database Changes

**Migration File:** `alembic/versions/2025_12_02_1400_make_email_nullable_and_non_unique.py`

```sql
-- 1. Drop UNIQUE constraint on email
DROP INDEX ix_users_email;

-- 2. Make email nullable
ALTER TABLE users ALTER COLUMN email DROP NOT NULL;

-- 3. Create non-unique index for performance
CREATE INDEX ix_users_email_nonunique ON users(email);

-- 4. Make name nullable
ALTER TABLE users ALTER COLUMN name DROP NOT NULL;
```

### Code Changes

#### 1. SQLAlchemy Model (`src/db/models.py`)
```python
class User(Base):
    cognito_user_id = Column(String, unique=True, nullable=False)  # ONLY unique ID
    email = Column(String, nullable=True, index=True)  # Removed unique=True
    name = Column(String, nullable=True)
```

#### 2. Pydantic Schema (`src/schemas/user.py`)
```python
class UserBase(BaseModel):
    email: Optional[EmailStr] = None  # Now optional
    name: Optional[str] = None
```

#### 3. Authentication Logic (`src/dependencies.py`)
- Removed fallback to `cognito_user_id` for email field
- Now stores `None` if email not provided
- Enhanced logging to handle NULL emails gracefully

#### 4. Frontend (`Morpheus-Marketplace-APP`)
- **TypeScript:** Made `email` optional in `CognitoUser` interface
- **NavUser Component:** Graceful fallbacks for missing email
  - Avatar: First letter of email or "U"
  - Display Name: name → email prefix → "User"
  - Display Email: email → "No email provided"
- **Account Page:** Already handled with `user?.email || "N/A"`

## Authentication Flow Examples

### Scenario 1: Email Available (Standard OAuth)
```
JWT Token: { sub: "abc-123", email: "alice@example.com" }
Database:  { cognito_user_id: "abc-123", email: "alice@example.com" }
Frontend:  Shows "alice@example.com" in navbar
```

### Scenario 2: Email Missing (Social Login - Email Permission Denied)
```
JWT Token: { sub: "xyz-789", email: undefined }
Database:  { cognito_user_id: "xyz-789", email: NULL }
Frontend:  Shows "User" / "No email provided" in navbar
```

### Scenario 3: Duplicate Email (Different Providers)
```
User A - Google:  { cognito_user_id: "google-123", email: "same@example.com" }
User B - Facebook: { cognito_user_id: "facebook-456", email: "same@example.com" }
Result: Both users exist independently, no conflict
```

## What Still Works

✅ **User Lookup:** Always by `cognito_user_id`  
✅ **API Keys:** Tied to `user_id`, encrypted with `cognito_user_id`  
✅ **Sessions:** Associated with `user_id` from JWT `sub` claim  
✅ **Frontend Display:** Uses JWT token data, not database  
✅ **Account Management:** All features work regardless of email presence

## Migration Instructions

### Development/Testing
```bash
cd /path/to/Morpheus-Marketplace-API
alembic upgrade head
```

### Production
```bash
# 1. Backup database first!
pg_dump morpheusapi > backup_before_social_login_$(date +%Y%m%d).sql

# 2. Run migration
cd /path/to/Morpheus-Marketplace-API
alembic upgrade head

# 3. Verify
psql morpheusapi -c "\d users"
# Should show: email | character varying | | | (not nullable = False, no unique constraint)
```

## Rollback (If Needed)

**WARNING:** Rollback will FAIL if:
- Any users have NULL email
- Duplicate emails exist in database

```bash
# Only if you need to rollback
alembic downgrade -1
```

## Testing Checklist

- [ ] User with email can login normally
- [ ] User with NULL email can login (social login)
- [ ] Two users with same email but different `cognito_user_id` can coexist
- [ ] Frontend displays graceful fallbacks for missing email
- [ ] API key creation/usage works for users without email
- [ ] Sessions created correctly for users without email
- [ ] Account page shows "N/A" for missing email
- [ ] Navbar shows "User" / "No email provided" for missing email

## Files Changed

### Backend
- `alembic/versions/2025_12_02_1400_make_email_nullable_and_non_unique.py` (NEW)
- `src/db/models.py` (MODIFIED)
- `src/schemas/user.py` (MODIFIED)
- `src/dependencies.py` (MODIFIED)
- `src/crud/user.py` (MODIFIED)

### Frontend
- `src/lib/types/cognito.ts` (MODIFIED)
- `src/components/nav-user.tsx` (MODIFIED)
- `src/lib/auth/cognito-direct-auth.ts` (MODIFIED)

## Key Takeaways

1. **cognito_user_id is king** - The ONLY unique identifier
2. **Email is optional metadata** - May be NULL, may be duplicate
3. **Frontend already JWT-based** - Database email is just a cache
4. **API keys properly isolated** - By user_id → cognito_user_id
5. **Graceful degradation** - System works with or without email

## Support

For issues or questions, check:
- Alembic migration logs
- Application logs for "user_creation" events
- Frontend console for JWT token parsing

