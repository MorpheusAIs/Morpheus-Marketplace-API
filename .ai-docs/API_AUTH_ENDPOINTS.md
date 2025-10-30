# API Authentication Requirements Reference

**Internal Document - Last Updated: 2025-10-24**

This document provides a comprehensive overview of all Swagger/OpenAPI endpoints in the Morpheus Marketplace API and their authentication requirements.

---

## 📋 Table of Contents

1. [Authentication Methods Overview](#authentication-methods-overview)
2. [Public Endpoints (No Auth Required)](#public-endpoints-no-auth-required)
3. [JWT Authentication Endpoints](#jwt-authentication-endpoints)
4. [API Key Authentication Endpoints](#api-key-authentication-endpoints)
5. [Recommendations for Cleanup](#recommendations-for-cleanup)

---

## Authentication Methods Overview

### 🔓 **No Authentication (Public)**
Endpoints accessible without any authentication token or API key.

### 🎫 **JWT Authentication (Bearer Token)**
Endpoints requiring Cognito OAuth2 JWT token obtained via the `/exchange-token` OAuth flow.
- **Header Format**: `Authorization: Bearer <cognito_jwt_token>`
- **Use Case**: User account management, API key creation, private key storage
- **Dependency**: `get_current_user()` from `src/dependencies.py`

### 🔑 **API Key Authentication**
Endpoints requiring an API key created by authenticated users.
- **Header Format**: `Authorization: Bearer <api_key>` or `X-API-Key: <api_key>`
- **Use Case**: AI model interactions, chat completions, session management
- **Dependency**: `get_api_key_user()` or `get_current_api_key()` from `src/dependencies.py`

---

## Public Endpoints (No Auth Required)

These endpoints are accessible without authentication:

### Root & Health Checks

| Method | Endpoint | Description | Location |
|--------|----------|-------------|----------|
| GET | `/` | API root information | `src/main.py:445` |
| GET | `/health` | API and database health check | `src/main.py:459` |
| GET | `/health/models` | Model service health check | `src/main.py:550` |
| GET | `/cors-check` | CORS configuration check | `src/main.py:611` |

### Documentation

| Method | Endpoint | Description | Location |
|--------|----------|-------------|----------|
| GET | `/docs` | Swagger UI (custom) | `src/main.py:936` |
| GET | `/api-docs` | API documentation landing | `src/main.py:1430` |
| GET | `/api/v1/openapi.json` | OpenAPI JSON schema | `src/main.py:1422` |
| GET | `/docs/oauth2-redirect` | OAuth2 redirect for Swagger | `src/main.py:697` |
| GET | `/exchange-token` | OAuth2 token exchange | `src/main.py:1273` |

### Model Information

| Method | Endpoint | Description | Location |
|--------|----------|-------------|----------|
| GET | `/api/v1/models` | List active models | `src/api/v1/models/index.py:19` |
| GET | `/api/v1/models/allmodels` | List all available models | `src/api/v1/models/index.py:97` |
| GET | `/api/v1/models/ratedbids` | Get rated bids for a model | `src/api/v1/models/index.py:169` |

**Note:** Model endpoints are public to allow users to browse available models before authentication.

---

## JWT Authentication Endpoints

These endpoints require a Cognito JWT Bearer token (`Authorization: Bearer <jwt_token>`):

### User Account Management

| Method | Endpoint | Description | Location |
|--------|----------|-------------|----------|
| GET | `/api/v1/auth/me` | Get current user information | `src/api/v1/auth/index.py:34` |
| DELETE | `/api/v1/auth/register` | Delete user account | `src/api/v1/auth/index.py:356` |

**Purpose:** User profile management and account deletion.

---

### API Key Management

| Method | Endpoint | Description | Location |
|--------|----------|-------------|----------|
| POST | `/api/v1/auth/keys` | Create new API key | `src/api/v1/auth/index.py:64` |
| GET | `/api/v1/auth/keys` | List all user API keys | `src/api/v1/auth/index.py:94` |
| DELETE | `/api/v1/auth/keys/{key_id}` | Delete specific API key | `src/api/v1/auth/index.py:114` |
| GET | `/api/v1/auth/keys/first` | Get first (oldest) API key | `src/api/v1/auth/index.py:469` |
| GET | `/api/v1/auth/keys/default` | Get user's default API key | `src/api/v1/auth/index.py:483` |
| PUT | `/api/v1/auth/keys/{key_id}/default` | Set API key as default | `src/api/v1/auth/index.py:497` |
| GET | `/api/v1/auth/keys/default/decrypted` | Get decrypted default API key | `src/api/v1/auth/index.py:518` |

**Purpose:** API key lifecycle management for users to create/manage keys used for AI operations.

---

### Private Key Management

| Method | Endpoint | Description | Location |
|--------|----------|-------------|----------|
| POST | `/api/v1/auth/private-key` | Store blockchain private key | `src/api/v1/auth/index.py:151` |
| GET | `/api/v1/auth/private-key` | Check private key status | `src/api/v1/auth/index.py:185` |
| DELETE | `/api/v1/auth/private-key` | Delete stored private key | `src/api/v1/auth/index.py:204` |

**Purpose:** Secure storage of user blockchain private keys for session creation and token approvals.

---

### Delegation Management

| Method | Endpoint | Description | Location |
|--------|----------|-------------|----------|
| POST | `/api/v1/auth/delegation` | Store signed delegation | `src/api/v1/auth/index.py:238` |
| GET | `/api/v1/auth/delegation` | List all delegations | `src/api/v1/auth/index.py:280` |
| GET | `/api/v1/auth/delegation/active` | Get active delegation | `src/api/v1/auth/index.py:303` |
| DELETE | `/api/v1/auth/delegation/{delegation_id}` | Delete delegation | `src/api/v1/auth/index.py:322` |

**Purpose:** Blockchain delegation management for gasless transactions.

---

## API Key Authentication Endpoints

These endpoints require an API key (`Authorization: Bearer <api_key>` or `X-API-Key: <api_key>`):

### Chat Completions

| Method | Endpoint | Description | Location | Auth Dependency |
|--------|----------|-------------|----------|----------------|
| POST | `/api/v1/chat/completions` | Create chat completion | `src/api/v1/chat/index.py:58` | `get_api_key_user` + `get_current_api_key` |

**Purpose:** OpenAI-compatible chat completions endpoint (streaming and non-streaming).

---

### Chat History Management

| Method | Endpoint | Description | Location | Auth Dependency |
|--------|----------|-------------|----------|----------------|
| POST | `/api/v1/chat-history/chats` | Create new chat | `src/api/v1/chat_history/index.py:83` | `get_current_user_api_key_only` |
| GET | `/api/v1/chat-history/chats` | List user chats | `src/api/v1/chat_history/index.py:100` | `get_current_user_api_key_only` |
| GET | `/api/v1/chat-history/chats/{chat_id}` | Get chat details | `src/api/v1/chat_history/index.py:126` | `get_current_user_api_key_only` |
| PUT | `/api/v1/chat-history/chats/{chat_id}` | Update chat title | `src/api/v1/chat_history/index.py:159` | `get_current_user_api_key_only` |
| DELETE | `/api/v1/chat-history/chats/{chat_id}` | Delete/archive chat | `src/api/v1/chat_history/index.py:179` | `get_current_user_api_key_only` |
| POST | `/api/v1/chat-history/chats/{chat_id}/messages` | Add message to chat | `src/api/v1/chat_history/index.py:197` | `get_current_user_api_key_only` |
| GET | `/api/v1/chat-history/chats/{chat_id}/messages` | Get chat messages | `src/api/v1/chat_history/index.py:235` | `get_current_user_api_key_only` |
| DELETE | `/api/v1/chat-history/messages/{message_id}` | Delete message | `src/api/v1/chat_history/index.py:259` | `get_current_user_api_key_only` |

**Purpose:** Persistent chat conversation storage and retrieval.

**Note:** Chat history was intentionally changed from JWT to API key authentication for consistency with chat completions. See `ai-docs/CHAT_STORAGE_AUTH_CONSISTENCY.md` for details.

---

### Session Management

| Method | Endpoint | Description | Location | Auth Dependency |
|--------|----------|-------------|----------|----------------|
| POST | `/api/v1/session/approve` | Approve token spending | `src/api/v1/session/index.py:43` | `get_api_key_user` |
| POST | `/api/v1/session/bidsession` | Create bid-based session | `src/api/v1/session/index.py:115` | `get_api_key_user` + `get_api_key_model` |
| POST | `/api/v1/session/modelsession` | Create model-based session | `src/api/v1/session/index.py:310` | `get_api_key_user` + `get_api_key_model` |
| POST | `/api/v1/session/closesession` | Close active session | `src/api/v1/session/index.py:412` | `get_api_key_user` + `get_api_key_model` |
| POST | `/api/v1/session/pingsession` | Ping session health | `src/api/v1/session/index.py:472` | `get_api_key_user` + `get_api_key_model` |

**Purpose:** Blockchain session lifecycle management (create, monitor, close sessions with AI providers).

---

### Automation Settings

| Method | Endpoint | Description | Location | Auth Dependency |
|--------|----------|-------------|----------|----------------|
| GET | `/api/v1/automation/settings` | Get automation settings | `src/api/v1/automation/index.py:41` | `get_current_user` |
| PUT | `/api/v1/automation/settings` | Update automation settings | `src/api/v1/automation/index.py:77` | `get_current_user` |

**Purpose:** User preferences for automatic session management across all API keys.

**✅ FIXED:** Changed from API key to JWT authentication for consistency with other user account settings.

---

### Embeddings

| Method | Endpoint | Description | Location | Auth Dependency |
|--------|----------|-------------|----------|----------------|
| POST | `/api/v1/embeddings/embeddings` | Create text embeddings | `src/api/v1/embeddings/index.py:29` | `get_api_key_user` + `get_current_api_key` |

**Purpose:** OpenAI-compatible text embeddings endpoint.

**✅ CORRECT:** Embeddings should use API key (consistent with chat completions).

---

## Recommendations for Cleanup

### 🔧 Changes to Consider

#### 1. **Automation Endpoints** ⚠️ HIGH PRIORITY

**Current State:**
- GET `/api/v1/automation/settings` - Uses API key (`get_api_key_user`)
- PUT `/api/v1/automation/settings` - Uses API key (`get_api_key_user`)

**Issue:** Automation settings are user preferences (like private keys and delegations), not API key operations.

**Recommendation:** Change to JWT authentication
```python
# In src/api/v1/automation/index.py
@router.get("/settings", response_model=AutomationSettings)
async def get_automation_settings(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)  # ← Change from get_api_key_user
):
```

**Reasoning:**
- Automation settings control session behavior across all API keys
- Should be managed at user account level (like private keys)
- Consistency with other user settings endpoints

---

#### 2. **Chat History Endpoints** ✅ ALREADY CORRECT

**Current State:** All chat history endpoints use API key authentication

**Previous Issue:** Originally used mixed JWT/API key authentication (inconsistent)

**Resolution:** Fixed in `CHAT_STORAGE_AUTH_CONSISTENCY.md` - now all chat operations use API key

**Reasoning:**
- Chat conversations belong to the API key that creates them
- Consistent with chat completions endpoint
- Frontend only needs one auth method for all chat operations

---

#### 3. **Model Endpoints** ✅ ALREADY CORRECT

**Current State:** All model endpoints are public (no authentication)

**Reasoning:**
- Users should be able to browse models before authenticating
- Model information is public blockchain data
- No sensitive user information exposed

---

### 📊 Summary of Authentication Distribution

| Category | No Auth | JWT Auth | API Key Auth |
|----------|---------|----------|--------------|
| **Root/Health** | 4 | 0 | 0 |
| **Documentation** | 5 | 0 | 0 |
| **Models** | 3 | 0 | 0 |
| **User Account** | 0 | 2 | 0 |
| **API Keys** | 0 | 7 | 0 |
| **Private Keys** | 0 | 3 | 0 |
| **Delegations** | 0 | 4 | 0 |
| **Automation** | 0 | 2 ✅ | 0 |
| **Chat Completions** | 0 | 0 | 1 |
| **Chat History** | 0 | 0 | 8 |
| **Sessions** | 0 | 0 | 5 |
| **Embeddings** | 0 | 0 | 1 |
| **TOTAL** | **12** | **18** | **15** |

---

### 🎯 Completed Actions

1. **✅ Moved Automation Endpoints to JWT** (Completed 2025-10-24)
   - Changed `get_api_key_user` → `get_current_user` in both automation endpoints
   - Updated docstrings to clearly indicate JWT Bearer authentication requirement
   - Added explanation that automation settings control behavior for all user's API keys
   - **Frontend Action Required:** Update to use JWT token for automation settings

2. **Swagger Documentation Status**
   - ✅ Docstrings updated with clear authentication requirements
   - ✅ OpenAPI schema includes all security schemes (OAuth2, BearerAuth, APIKeyAuth)
   - ℹ️ Current implementation shows all auth methods as alternatives in Swagger UI
   - ℹ️ Actual enforcement is correct via FastAPI dependencies

3. **Recommended: Frontend Authentication Guide**
   - Create clear guide showing:
     - When to use JWT (user settings, account management, API keys, private keys, delegations, automation)
     - When to use API key (AI operations, chat, embeddings, sessions, chat history)

---

## Dependency Reference

### Authentication Dependencies in `src/dependencies.py`

```python
# JWT Authentication - Requires Cognito Bearer Token
async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: Optional[HTTPAuthorizationCredentials] = Depends(oauth2_scheme_optional)
) -> User

# API Key Authentication - Requires API Key
async def get_api_key_user(
    db: AsyncSession = Depends(get_db),
    api_key: str = Security(api_key_header)
) -> Optional[User]

async def get_current_api_key(
    db: AsyncSession = Depends(get_db),
    api_key_str: str = Security(api_key_header)
) -> APIKey

async def get_api_key_model(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(api_key_header)
) -> Optional[APIKey]
```

---

## Testing Authentication

### JWT Token (Cognito)
```bash
# Get token via OAuth flow
curl https://api.mor.org/exchange-token?code=<auth_code>

# Use JWT for user endpoints
curl -H "Authorization: Bearer <jwt_token>" \
     https://api.mor.org/api/v1/auth/me
```

### API Key
```bash
# Create API key (requires JWT)
curl -X POST -H "Authorization: Bearer <jwt_token>" \
     -H "Content-Type: application/json" \
     -d '{"name": "My API Key"}' \
     https://api.mor.org/api/v1/auth/keys

# Use API key for chat
curl -X POST -H "Authorization: Bearer <api_key>" \
     -H "Content-Type: application/json" \
     -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}' \
     https://api.mor.org/api/v1/chat/completions
```

---

## Version History

| Date | Changes |
|------|---------|
| 2025-10-24 | Initial documentation - comprehensive endpoint authentication audit |
| 2025-10-24 | **COMPLETED:** Changed automation endpoints from API key to JWT authentication |

---

**Document Status:** ✅ Implemented  
**Completed Actions:**
1. ✅ Changed automation endpoints from API key (`get_api_key_user`) to JWT (`get_current_user`) authentication
2. ✅ Updated docstrings with clear authentication requirements
3. ✅ Updated authentication reference documentation

**Remaining Actions:**
1. ⚠️ **Frontend Update Required:** Frontend must be updated to use JWT token for automation settings endpoints
2. 📝 Consider creating frontend authentication guide for developers

