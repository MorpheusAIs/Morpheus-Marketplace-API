# Database Architecture Summary

## Overview
This document provides a comprehensive overview of the PostgreSQL database architecture for the Morpheus API Gateway system. This information is intended to support the migration from internal PostgreSQL to external databases for better load management and data architecture.

## Database Connection Configuration
- **Engine**: PostgreSQL with AsyncIO support
- **Connection**: SQLAlchemy async engine with connection pooling
- **Session Management**: AsyncSession with expire_on_commit=False
- **Connection String**: Configured via `DATABASE_URL` environment variable

## Database Tables and Relationships

### Entity Relationship Diagram (ASCII)
```
                              ┌─────────────────┐
                              │     users       │
                              │─────────────────│
                              │ id (PK)         │──┐
                              │ name            │  │
                              │ email (UQ)      │  │
                              │ hashed_password │  │
                              │ is_active       │  │
                              │ created_at      │  │
                              │ updated_at      │  │
                              └─────────────────┘  │
                                                   │
                    ┌──────────────┬──────────────┼─────────────────┬────────────────┐
                    │              │              │                 │                │
                    ▼              ▼              ▼                 ▼                ▼
            ┌─────────────┐ ┌──────────────┐ ┌──────────────┐ ┌───────────────┐ ┌─────────────┐
            │  api_keys   │ │  sessions    │ │user_private  │ │user_auto      │ │delegations  │
            │─────────────│ │──────────────│ │   _keys      │ │ _settings     │ │─────────────│
            │ id (PK)     │ │ id (PK)      │ │──────────────│ │───────────────│ │ id (PK)     │
            │ key_prefix  │ │ user_id(FK)  │ │ id (PK)      │ │ user_id(FK,UQ)│ │ user_id(FK) │
            │ hashed_key  │ │api_key_id(FK)│ │user_id(FK,UQ)│ │user_id(FK,UQ) │ │delegate_addr│
            │ user_id(FK) │ │ model        │ │encrypted_key │ │ is_enabled    │ │signed_deleg │
            │ name        │ │ type         │ │encryption_   │ │session_dur    │ │ expiry      │
            │ created_at  │ │ created_at   │ │ _metadata    │ │ created_at    │ │ created_at  │
            │ last_used_at│ │ expires_at   │ │ created_at   │ │ updated_at    │ │ is_active   │
            │ is_active   │ │ is_active    │ │ updated_at   │ └───────────────┘ └─────────────┘
            └─────────────┘ └──────────────┘ └──────────────┘
                    │              │
                    └──────────────┘
                 (1:1 active session constraint)
```

## Core vs Placeholder Features

### 🔧 **Currently Active Tables (Core System)**
The system currently operates with these **4 core tables** that handle all primary functionality:

1. **`users`** - Authentication and account management ✅
2. **`api_keys`** - API authentication and access control ✅  
3. **`sessions`** - Blockchain session management ✅
4. **`user_automation_settings`** - Session automation control ✅

**These 4 tables provide complete functionality for:**
- User registration and login
- API key generation and management
- Session creation and management with blockchain integration
- Chat completions and AI interactions
- **Automated session management** (controlled by user_automation_settings)
- **Blockchain operations** (using single fallback private key for all users)

### 🚧 **Placeholder Tables (Future Features)**
The following **2 tables** are implemented but not currently required for core operations:

1. **`user_private_keys`** - For individual user private key storage 🔮
2. **`delegations`** - For blockchain delegation functionality 🔮

#### **user_private_keys - Future Individual Key Management**
**Current Status:**
- ✅ Table exists and has CRUD operations implemented
- ✅ API endpoints exist for private key management (`/auth/private-key`)
- ⚠️ **Currently unused in production** - system uses `FALLBACK_PRIVATE_KEY` for all users
- 🔮 **Reserved for future functionality** when individual user private keys are needed

**Why it exists:**
- **Future user autonomy**: Allow users to manage their own blockchain private keys
- **Enhanced security**: Move from shared fallback key to individual user keys
- **Advanced features**: Enable user-specific blockchain operations
- **Development planning**: Table is designed and tested but not in critical path

**Current Implementation:**
- System checks for user private key first, then falls back to `FALLBACK_PRIVATE_KEY`
- **All current operations use the fallback key** (development/production)
- Individual key storage capability exists but is not actively utilized

#### **delegations - Future Blockchain Features**
**Current Status:**
- ✅ Table exists and has CRUD operations implemented
- ✅ API endpoints exist for delegation management
- ⚠️ **Currently unused** - system works without delegations
- 🔮 **Reserved for future blockchain delegation features**

### 🎯 **System Architecture Reality**

**Current Flow (Active):**
```
User → API Key → [Automation Settings Check] → [FALLBACK_PRIVATE_KEY] → Session → Chat Completion
```

**Private Key Resolution Logic (Current Implementation):**
```python
# Code from get_private_key_with_fallback():
1. Try to get user's private key from database (currently returns None)
2. Use FALLBACK_PRIVATE_KEY (currently always happens)
3. All users share the same blockchain private key via fallback
```

**Key Dependencies:**
- ✅ **user_automation_settings**: Checked on EVERY `/chat/completions` request to determine if automated session creation should occur
- ✅ **FALLBACK_PRIVATE_KEY**: Currently used for ALL blockchain session creation
- ✅ **sessions**: Core session management for all AI interactions
- ✅ **api_keys**: Authentication for all requests
- ✅ **users**: Foundation for all user operations

**This means:**
- ✅ **4 tables are actively required** for all current functionality
- ✅ **user_automation_settings controls session behavior** on every chat request
- ✅ **All blockchain operations use shared fallback private key**
- 🔮 **user_private_keys and delegations are future features** with no current operational impact
- ✅ **System is fully functional with just 4 core tables + fallback environment variable**

### 📊 **Corrected Usage Analysis**

#### **High-Traffic Tables (Every Request)**
1. **api_keys** - Authentication on every request
2. **sessions** - Session management and tracking
3. **user_automation_settings** - Checked on every chat completion request

#### **Medium-Traffic Tables (User Management)**
4. **users** - User authentication and session association

#### **Future Feature Tables (Zero Current Traffic)**
5. **user_private_keys** - Individual key management (future use)
6. **delegations** - Blockchain delegation features (future use)

### 🚨 **Current vs Future Architecture**

#### **Current Production System (4 Tables + Environment Variable)**
- **Required**: users, api_keys, sessions, user_automation_settings
- **Environment**: `FALLBACK_PRIVATE_KEY` (shared blockchain key)
- **Optional**: user_private_keys, delegations (exist but unused)
- **Benefit**: Simple architecture with shared blockchain identity

#### **Future Enhanced System (6 Tables)**
- **Required**: users, api_keys, sessions, user_automation_settings, user_private_keys
- **Optional**: delegations (when delegation features activate)
- **Benefit**: Individual user blockchain identities and advanced features
- **Migration Path**: Transition from fallback key to individual user keys

## Table Structures

### 1. users
**Purpose**: Core user authentication and account management
**Migration History**: 
- Created in initial migration (d4ae65008d6d)
- Added `is_active` field (69491a79cfd0)
- Added `updated_at` field (5f7a3e1b8d42)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, INDEX | Auto-incrementing user ID |
| name | STRING | NULLABLE | User display name |
| email | STRING | UNIQUE, INDEX | User email for authentication |
| hashed_password | STRING | NULLABLE | Bcrypt hashed password |
| is_active | BOOLEAN | DEFAULT TRUE | Account status flag |
| created_at | DATETIME | DEFAULT utcnow() | Account creation timestamp |
| updated_at | DATETIME | DEFAULT utcnow(), ON UPDATE | Last modification timestamp |

**Relationships**:
- One-to-Many: api_keys, sessions, delegations
- One-to-One: user_private_keys, user_automation_settings

### 2. api_keys
**Purpose**: API key management for authentication
**Migration History**:
- Created in initial migration (d4ae65008d6d)
- Added `name` field (3ec3925c8904)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, INDEX | Auto-incrementing API key ID |
| key_prefix | STRING | INDEX | First 9 chars of API key (sk-xxxxxx) |
| hashed_key | STRING | NULLABLE | SHA-256 hash of full API key |
| user_id | INTEGER | FOREIGN KEY(users.id) | Owner of the API key |
| name | STRING | NULLABLE | Optional descriptive name |
| created_at | DATETIME | DEFAULT utcnow() | Key creation timestamp |
| last_used_at | DATETIME | NULLABLE | Last usage timestamp |
| is_active | BOOLEAN | DEFAULT TRUE | Key status flag |

**Relationships**:
- Many-to-One: users (via user_id)
- One-to-Many: sessions

### 3. sessions
**Purpose**: Blockchain session management and tracking
**Migration History**:
- Originally created as `user_sessions` (7c29c35fc9bc)
- Constraints fixed (fix_session_constraints)
- Consolidated and recreated as `sessions` (881e615d25ac)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | STRING | PRIMARY KEY | Blockchain session ID |
| user_id | INTEGER | FOREIGN KEY(users.id), NULLABLE | Associated user |
| api_key_id | INTEGER | FOREIGN KEY(api_keys.id), INDEX | Associated API key |
| model | STRING | NOT NULL | Model name or blockchain ID |
| type | STRING | NOT NULL | "automated" or "manual" |
| created_at | DATETIME | DEFAULT utcnow() | Session creation time |
| expires_at | DATETIME | NOT NULL | Session expiration time |
| is_active | BOOLEAN | DEFAULT TRUE, INDEX | Session status |

**Constraints**:
- UNIQUE INDEX: `sessions_active_api_key_unique` on (api_key_id, is_active) WHERE is_active IS TRUE
  - Ensures only one active session per API key

**Relationships**:
- Many-to-One: users (via user_id), api_keys (via api_key_id)

### 4. user_private_keys
**Purpose**: Encrypted blockchain private key storage
**Migration History**: Created in initial migration (d4ae65008d6d)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, INDEX | Auto-incrementing key ID |
| user_id | INTEGER | FOREIGN KEY(users.id), UNIQUE | Owner (one key per user) |
| encrypted_private_key | LARGEBINARY | NULLABLE | AES encrypted private key |
| encryption_metadata | JSON | NULLABLE | Salt, algorithm, key derivation info |
| created_at | DATETIME | DEFAULT utcnow() | Key storage timestamp |
| updated_at | DATETIME | DEFAULT utcnow(), ON UPDATE | Last update timestamp |

**Relationships**:
- One-to-One: users (via user_id)

### 5. user_automation_settings
**Purpose**: User-specific session automation configuration
**Migration History**: Created in delegation migration (d00825f2a89a)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, INDEX | Auto-incrementing settings ID |
| user_id | INTEGER | FOREIGN KEY(users.id), UNIQUE | Owner (one setting per user) |
| is_enabled | BOOLEAN | DEFAULT FALSE | Automation enabled flag |
| session_duration | INTEGER | DEFAULT 3600 | Session duration in seconds |
| created_at | DATETIME | DEFAULT utcnow() | Settings creation time |
| updated_at | DATETIME | DEFAULT utcnow(), ON UPDATE | Last modification time |

**Relationships**:
- One-to-One: users (via user_id)

### 6. delegations
**Purpose**: Blockchain delegation data storage
**Migration History**:
- Initially created and dropped in (d00825f2a89a)
- Recreated in consolidation migration (881e615d25ac)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, INDEX | Auto-incrementing delegation ID |
| user_id | INTEGER | FOREIGN KEY(users.id), INDEX | Delegating user |
| delegate_address | STRING | NOT NULL, INDEX | Blockchain address of delegate |
| signed_delegation_data | TEXT | NOT NULL | EIP-712 signed delegation object |
| expiry | DATETIME | NULLABLE | Optional delegation expiration |
| created_at | DATETIME(timezone=True) | DEFAULT now() | Delegation creation time |
| is_active | BOOLEAN | DEFAULT TRUE, INDEX | Delegation status |

**Relationships**:
- Many-to-One: users (via user_id)

## Database Access Patterns

### CRUD Operations by Entity

#### Users (`src/crud/user.py`)
**Functions accessing database:**
- `get_user_by_id(db, user_id)` - SELECT by ID
- `get_user_by_email(db, email)` - SELECT by email (unique lookup)
- `create_user(db, user_in)` - INSERT new user with hashed password
- `update_user(db, db_user, user_in)` - UPDATE user fields
- `authenticate_user(db, email, password)` - SELECT + password verification
- `get_all_users(db, skip, limit)` - SELECT with pagination
- `delete_user(db, user_id)` - DELETE user and cascade

#### API Keys (`src/crud/api_key.py`)
**Functions accessing database:**
- `get_api_key_by_id(db, api_key_id)` - SELECT with user join
- `get_api_key_by_prefix(db, key_prefix)` - SELECT by prefix for authentication
- `create_api_key(db, user_id, api_key_in)` - INSERT with key generation
- `get_user_api_keys(db, user_id)` - SELECT all keys for user
- `deactivate_api_key(db, api_key_id, user_id)` - UPDATE is_active to false

#### Sessions (`src/crud/session.py`)
**Functions accessing database:**
- `get_active_session_by_api_key(db, api_key_id)` - SELECT active session
- `get_all_active_sessions(db)` - SELECT all active sessions
- `get_session_by_id(db, session_id)` - SELECT by session ID
- `create_session(db, session_id, ...)` - INSERT new session
- `mark_session_inactive(db, session_id)` - UPDATE is_active to false
- `get_session_by_api_key_id(db, api_key_id)` - SELECT by API key

#### Private Keys (`src/crud/private_key.py`)
**Functions accessing database:**
- `get_user_private_key(db, user_id)` - SELECT encrypted key
- `create_user_private_key(db, user_id, private_key)` - INSERT/REPLACE encrypted key
- `delete_user_private_key(db, user_id)` - DELETE user's key
- `user_has_private_key(db, user_id)` - EXISTS check
- `get_private_key_with_fallback(db, user_id)` - SELECT with fallback logic

#### Automation Settings (`src/crud/automation.py`)
**Functions accessing database:**
- `create_automation_settings(db, user_id, ...)` - INSERT settings
- `get_automation_settings(db, user_id)` - SELECT by user
- `update_automation_settings(db, user_id, ...)` - UPDATE settings
- `delete_automation_settings(db, user_id)` - DELETE settings

#### Delegations (`src/crud/delegation.py`)
**Functions accessing database:**
- `create_user_delegation(db, delegation, user_id)` - INSERT delegation
- `get_active_delegation_by_user(db, user_id)` - SELECT active delegation
- `set_delegation_inactive(db, delegation)` - UPDATE is_active to false
- `get_delegations_by_user(db, user_id, skip, limit)` - SELECT with pagination

## API Endpoint Database Access

### Authentication Endpoints (`/api/v1/auth`)
**Database Operations:**
- `POST /register` → user_crud.create_user()
- `POST /login` → user_crud.authenticate_user()
- `GET /keys` → api_key_crud.get_user_api_keys()
- `POST /keys` → api_key_crud.create_api_key()
- `DELETE /keys/{key_id}` → api_key_crud.deactivate_api_key()
- `POST /private-key` → private_key_crud.create_user_private_key()
- `DELETE /private-key` → private_key_crud.delete_user_private_key()
- `POST /delegation` → delegation_crud.create_user_delegation()

### Chat Endpoints (`/api/v1/chat`)
**Database Operations:**
- `POST /completions` → 
  - session_crud.get_session_by_api_key_id()
  - session_service.create_automated_session()
  - automation_crud.get_automation_settings()
  - api_key_crud.get_api_key_by_prefix()

### Session Endpoints (`/api/v1/session`)
**Database Operations:**
- `POST /initialize` → session_service.create_automated_session()
- `POST /pingsession` → session_crud.get_active_session_by_api_key()
- `DELETE /close` → session_crud.mark_session_inactive()

### Automation Endpoints (`/api/v1/automation`)
**Database Operations:**
- `GET /settings` → automation_crud.get_automation_settings()
- `PUT /settings` → automation_crud.update_automation_settings()

### Models Endpoints (`/api/v1/models`)
**Database Operations:**
- No direct database access (proxies to external API)

## Background Tasks

### Session Cleanup Task (`src/main.py:cleanup_expired_sessions`)
**Database Operations:**
- Runs every 15 minutes
- `SELECT sessions WHERE is_active = TRUE AND expires_at < NOW()`
- `session_service.close_session()` for expired sessions
- `session_service.synchronize_sessions()` for state sync

### Model Sync Task (`src/core/model_sync.py`)
**Database Operations:**
- No direct database access
- Manages external model data in JSON files

## Dependencies and Middleware

### Database Session Dependency (`src/db/database.py:get_db`)
- Provides AsyncSession for each request
- Handles commit/rollback automatically
- Used by all database-dependent endpoints

### Authentication Dependencies
- `get_api_key_user()` → api_key_crud.get_api_key_by_prefix() + user lookup
- `get_current_api_key()` → api_key_crud.get_api_key_by_prefix()

## External Database Migration Considerations

### High-Traffic Tables
1. **sessions** - Frequent reads/writes during chat operations
2. **api_keys** - Read on every authenticated request
3. **users** - Moderate access for authentication

### Low-Traffic Tables
1. **user_private_keys** - Occasional reads for session creation
2. **user_automation_settings** - Infrequent reads/updates
3. **delegations** - Rare writes, occasional reads

### Suggested Migration Strategy

#### Phase 1: Core System Migration (Critical Priority)
**Migrate the 4 currently active tables that handle ALL functionality:**
- `users` - Authentication foundation
- `api_keys` - API access control (high traffic)
- `sessions` - Active session management (highest traffic)
- `user_automation_settings` - Session automation control (checked every chat request)

**Environment Variable (Critical):**
- Ensure `FALLBACK_PRIVATE_KEY` is properly configured in external environment
- This single key currently handles ALL blockchain operations for ALL users

**Benefits:**
- ✅ Maintains complete system functionality with minimal complexity
- ✅ Reduces load on internal database immediately  
- ✅ All user flows preserved (authentication, sessions, chat completions)
- ✅ Simple migration with only 4 tables + 1 environment variable
- ✅ Zero feature regression - system works identically

#### Phase 2: High-Performance Session Database
- Move `sessions` table to dedicated high-performance database
- Optimize for frequent reads/writes and automatic cleanup
- Consider Redis or similar for session caching
- This table has the highest traffic volume and cleanup requirements

#### Phase 3: Security-Optimized User Database  
- Move `users` and `api_keys` to security-focused database
- Implement enhanced encryption and access controls
- Focus on compliance and audit requirements
- These contain authentication data

#### Phase 4: Automation Settings Database
- Move `user_automation_settings` to performance-optimized database
- Could be co-located with session database for efficiency
- Optimize for fast reads (checked on every chat request)
- Consider caching layer for automation settings

#### Phase 5: Future Feature Implementation (When Ready)
**Individual User Private Keys:**
- Implement `user_private_keys` table when ready to move away from shared fallback key
- Create migration path from `FALLBACK_PRIVATE_KEY` to individual user keys
- Enhance security by giving each user their own blockchain identity
- This is optional and doesn't affect current functionality

**Blockchain Delegations:**
- Implement `delegations` table when delegation features are needed
- Can remain unimplemented indefinitely without impact
- Enable advanced blockchain delegation features

**Benefits:**
- 🔄 Current system operates perfectly with just 4 tables
- 🔄 Future features can be added incrementally without disruption
- 🔄 Allows focus on essential migration first
- 🔄 No rush to implement unused features

#### 🎯 **Simplified Migration Reality**

**Current State:**
```
4 Database Tables + 1 Environment Variable = Complete Functionality
```

**Migration Complexity:**
- **Immediate Need**: Migrate 4 core tables only
- **Future Enhancement**: Add individual user features when desired
- **Zero Risk**: System maintains full functionality throughout migration

**Risk Assessment:**
```
Migration Risk: LOW (only 4 tables required)
Feature Risk: ZERO (no functionality changes)
Complexity: MINIMAL (straightforward table migration)
```

## Database Performance Considerations

#### Current Indexes (Core Tables)
**High Priority for Migration:**
- `users.email` (unique) - Authentication lookups
- `api_keys.key_prefix` - API authentication  
- `sessions.api_key_id` - Session retrieval
- `sessions.is_active` - Active session filtering
- `sessions_active_api_key_unique` (composite unique) - Constraint enforcement

**Placeholder Table Indexes (Lower Priority):**
- `delegations.user_id` - User delegation lookups
- `delegations.delegate_address` - Address-based queries  
- `delegations.is_active` - Active delegation filtering

#### Recommended Additional Indexes for External Migration
**Core System Optimization:**
- `sessions.expires_at` (for cleanup queries) - **High Priority**
- `sessions.created_at` (for analytics) - Medium Priority
- `api_keys.last_used_at` (for usage analytics) - Medium Priority
- `users.created_at` (for user analytics) - Low Priority

**Future Feature Optimization (when activated):**
- `user_private_keys.created_at` (for key rotation analytics)
- `user_automation_settings.updated_at` (for settings change tracking)

## Security Considerations for Migration

### Sensitive Data by Priority

#### Core System (High Security Priority):
- `users.hashed_password` - Bcrypt hashes for authentication
- `api_keys.hashed_key` - SHA-256 hashes for API access

#### Placeholder Features (Future Security Requirements):
- `user_private_keys.encrypted_private_key` - AES encrypted blockchain keys
- `user_private_keys.encryption_metadata` - Encryption parameters
- `delegations.signed_delegation_data` - Blockchain delegation signatures

### Migration Security Strategy

#### Core Tables:
- **Immediate encryption at rest** for users and api_keys tables
- **Connection encryption** (TLS) for all database connections
- **Access logging** for all authentication-related queries
- **Backup encryption** for user data and API keys

### Access Control Strategy
- **Core tables**: Immediate database-level access controls
- **Connection pooling** with authentication for all external databases
- **Rate limiting** on authentication endpoints
- **Monitoring and alerting** for unusual access patterns
- **Placeholder tables**: Can use standard security until features activate