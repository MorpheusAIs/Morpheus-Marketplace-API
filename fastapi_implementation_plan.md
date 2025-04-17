# Morpheus API Gateway - FastAPI Implementation Plan

## 1. Goal

To migrate the existing Morpheus API Gateway functionality from Node.js/Express to Python/FastAPI, incorporating robust authentication, persistent storage, secure key management, and best practices for API development and deployment using Docker.

## 2. Technology Stack

*   **Web Framework:** FastAPI
*   **Data Validation:** Pydantic
*   **Database ORM:** SQLAlchemy (with Alembic for migrations)
*   **Database:** PostgreSQL
*   **Caching/Key Storage:** Redis
*   **Asynchronous HTTP Client:** `httpx` (for communicating with the proxy-router)
*   **JWT Handling:** `python-jose`
*   **Password Hashing:** `passlib[bcrypt]`
*   **Cryptography:** `cryptography` (for private key encryption)
*   **Containerization:** Docker, Docker Compose
*   **Configuration Management:** Pydantic Settings

## 3. Core API Structure (Proposed Modules)

```
morpheus_api_python/
├── alembic/                  # Database migrations
├── alembic.ini
├── src/
│   ├── api/                  # FastAPI routers/endpoints
│   │   ├── v1/
│   │   │   ├── auth.py       # User registration, login, API keys, private key mgmt
│   │   │   ├── models.py     # OpenAI compatible models endpoint
│   │   │   └── chat.py       # OpenAI compatible chat completions
│   │   └── __init__.py
│   ├── core/                 # Core logic, configuration, security
│   │   ├── config.py       # Pydantic settings
│   │   ├── security.py     # JWT generation/validation, password hashing, API key handling
│   │   ├── key_vault.py    # Private key encryption/decryption, KMS interaction
│   │   └── __init__.py
│   ├── crud/                 # Database interaction functions (Create, Read, Update, Delete)
│   │   ├── user.py
│   │   ├── api_key.py
│   │   ├── private_key.py    # Or integrate into user.py/key_vault.py
│   │   └── __init__.py
│   ├── db/                   # Database session management, base model
│   │   ├── database.py
│   │   ├── models.py       # SQLAlchemy models
│   │   └── __init__.py
│   ├── schemas/              # Pydantic schemas for request/response validation
│   │   ├── user.py
│   │   ├── token.py
│   │   ├── api_key.py
│   │   ├── private_key.py
│   │   ├── openai.py       # Schemas for OpenAI compatibility
│   │   └── __init__.py
│   ├── services/             # Business logic layer
│   │   ├── redis_client.py # Redis interactions (caching, potentially encrypted keys)
│   │   ├── model_mapper.py # Mapping OpenAI model names <-> Blockchain IDs
│   │   ├── proxy_router.py # Interaction logic with the Morpheus-Lumerin Node proxy-router
│   │   ├── session_manager.py # Logic for handling Morpheus sessions (if needed beyond proxy-router interaction)
│   │   └── __init__.py
│   ├── dependencies.py       # FastAPI dependency injection functions (e.g., get_db, get_current_user)
│   └── main.py               # FastAPI application instance and root setup
├── .env.example              # Environment variable template
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md                 # Updated README reflecting FastAPI implementation
```

## 4. Detailed Feature Implementation

### 4.1. User Authentication & Authorization

*   **Endpoints:**
    *   `POST /auth/register`: Create new user (name, email, password). Hash password using `passlib`. Store in PostgreSQL.
    *   `POST /auth/login`: Authenticate user (email, password). Verify password hash. Return JWT access and refresh tokens (`python-jose`).
    *   `POST /auth/refresh`: Use refresh token to get a new access token.
*   **Security:** Implement OAuth2PasswordBearer flow with JWT. Access tokens are short-lived, refresh tokens are longer-lived and stored securely (e.g., HTTPOnly cookie or potentially DB).
*   **Dependencies:** `get_current_user` dependency to protect endpoints, validating JWT and retrieving user data from the DB.

### 4.2. API Key Management

*   **Endpoints (protected):**
    *   `POST /auth/keys`: Generate a new API key associated with the authenticated user. Store securely (e.g., hash the key or store metadata only) in PostgreSQL, linked to the user ID.
    *   `GET /auth/keys`: List API keys (or metadata like prefix, creation date) for the authenticated user.
    *   `DELETE /auth/keys/{key_prefix}`: Revoke an API key.
*   **Authentication:** Implement an `APIKeyHeader` security scheme. Requests to OpenAI-compatible endpoints will use this key. The gateway will look up the key, verify it, and identify the associated user.

### 4.3. Private Key Management

*   **Endpoints (protected):**
    *   `POST /auth/private-key`: Allow authenticated user (via JWT) to submit their blockchain private key.
    *   `GET /auth/private-key/status`: Check if a private key is registered for the user associated with the JWT/API key.
    *   `DELETE /auth/private-key`: Delete the stored private key for the user.
*   **Storage:**
    1.  Retrieve the master encryption key from KMS (`core/key_vault.py`).
    2.  Encrypt the user's submitted private key using a strong algorithm (e.g., Fernet from `cryptography`) with the master key.
    3.  Store the *encrypted* private key in the PostgreSQL database, associated with the user ID. **Avoid storing encrypted keys directly in Redis** for better persistence and relational integrity, unless specific performance requirements dictate otherwise (and even then, carefully consider security).
*   **Retrieval:** When a request requires the private key (e.g., for proxy-router interaction), retrieve the *encrypted* key from the DB based on the authenticated user, fetch the master key from KMS, and decrypt the private key *in memory* just before use. Do not store the decrypted key persistently.

### 4.4. OpenAI Compatibility Layer

*   **Endpoints:**
    *   `GET /v1/models`: Authenticate via API Key. Fetch available models (potentially from `proxy-router` or a cached list managed by `services/model_mapper.py`). Format according to OpenAI spec using Pydantic schemas (`schemas/openai.py`).
    *   `POST /v1/chat/completions`: Authenticate via API Key.
        1.  Identify the user associated with the API key.
        2.  Retrieve and decrypt the user's private key (`core/key_vault.py`).
        3.  Map the requested OpenAI model name to a blockchain model ID (`services/model_mapper.py`).
        4.  Interact with the `proxy-router` service (`services/proxy_router.py`) using the decrypted private key and necessary session logic (see 4.6).
        5.  Handle streaming and non-streaming responses, formatting them according to OpenAI spec (`schemas/openai.py`).
*   **Schemas:** Define Pydantic models in `schemas/openai.py` mirroring OpenAI request/response structures.

### 4.5. Proxy-Router Interaction

*   **Module:** `services/proxy_router.py`
*   **Functionality:** Use `httpx` for asynchronous communication with the Morpheus-Lumerin Node `proxy-router`. Implement functions to handle requests specific to the `proxy-router`'s API, including authentication using the user's dynamically decrypted private key. Abstract the details of `proxy-router` communication here.

### 4.6. Session Management (Morpheus Node Context)

*   **Clarification Needed:** The exact mechanism of "session creation/management" mentioned in the original README needs clarification in the context of the "Key Rotation" architecture.
*   **Assumption:** The "session" might refer to the context established with the `proxy-router` using a specific private key for a series of operations, or perhaps an approval mechanism.
*   **Implementation:**
    *   If sessions are implicit (handled by `proxy-router` based on authenticated requests with the private key), this module might be minimal or integrated into `services/proxy_router.py`.
    *   If explicit session creation/closing calls to the `proxy-router` are needed, implement them in `services/session_manager.py` or `proxy_router.py`.
    *   The `POST /auth/approve-spending` endpoint needs implementation, likely involving an interaction with the blockchain/`proxy-router` using the user's decrypted private key.

### 4.7. Caching

*   **Module:** `services/redis_client.py`
*   **Functionality:** Use Redis for caching:
    *   Model mappings (OpenAI name <-> Blockchain ID).
    *   Potentially, active `proxy-router` session details (if applicable).
    *   **Avoid caching decrypted private keys.**
*   **Configuration:** Ensure Redis connection details (including password) are loaded securely from environment variables (`core/config.py`).

### 4.8. Error Handling

*   Implement FastAPI exception handlers to catch common errors (validation errors, HTTP exceptions, custom exceptions) and return standardized JSON error responses, ideally matching the OpenAI error format where applicable.

### 4.9. Configuration

*   Use Pydantic's `BaseSettings` in `core/config.py` to load configuration from environment variables (`.env` file for local dev). Include settings for database URL, Redis URL (with password), JWT secrets, KMS details, `proxy-router` URL, etc.

## 5. Database Schema (High-Level - `src/db/models.py`)

*   **User:** `id`, `name`, `email` (unique), `hashed_password`, `created_at`, `updated_at`. Relationships to API Keys and Private Key.
*   **APIKey:** `id`, `key_prefix` (for identification), `hashed_key` (or store metadata only if verification happens elsewhere), `user_id` (foreign key), `created_at`, `last_used_at`, `is_active`.
*   **UserPrivateKey:** `id`, `user_id` (foreign key, unique), `encrypted_private_key` (bytes or string), `encryption_metadata` (e.g., algorithm, IV if needed), `created_at`, `updated_at`.

*   Use Alembic (`alembic/`) for managing database schema migrations.

## 6. Docker Setup

*   **`Dockerfile`:** Multi-stage build for a lean production image. Installs Python dependencies, copies application code. Runs using an ASGI server like Uvicorn with Gunicorn workers.
*   **`docker-compose.yml`:** Defines services for:
    *   `api`: The FastAPI application.
    *   `db`: PostgreSQL database, potentially mounting a volume for persistence.
    *   `redis`: Redis instance, configuring password authentication and potentially mounting a volume.
    *   Sets up network, environment variables (can link to `.env` file).
*   **Migrations:** Include a command or separate service in `docker-compose.yml` to run Alembic migrations on startup.

## 7. Recommendations Recap

*   **Authentication Store:** Use **PostgreSQL** via **SQLAlchemy** for persistent storage of users and API keys.
*   **KMS:** Integrate with a dedicated **Key Management Service** (e.g., AWS KMS, Google Cloud KMS, Azure Key Vault, HashiCorp Vault) to manage the master key used for encrypting/decrypting user private keys. **Do not** store the master key in `.env` or config files for production.
*   **Redis Authentication:** **Enable password authentication** on the Redis instance (`requirepass` directive) and configure the FastAPI client to use the password. This is crucial for security, even within Docker networks.

## 8. Open Questions / Areas Needing Clarification

1.  **Proxy-Router API Specifics:** What is the exact API contract of the `morpheus-lumerin-node` `proxy-router`? Specifically:
    *   How is authentication performed (e.g., request signing with the private key, passing the key in a header)?
    *   How are requests for model inference structured?
    *   What are the details of the streaming protocol, if any?
    *   Is there an explicit "session" concept that the gateway needs to manage with the router, or is each request self-contained using the provided key?
2.  **Model Mapping Source:** Where does the definitive mapping between OpenAI model names (e.g., `gpt-4`) and Morpheus blockchain model IDs come from? Is it fetched dynamically from the `proxy-router`, a configuration file, or another source? How often does it need refreshing?
3.  **Token Spending Approval:** What is the exact mechanism for the `POST /auth/approve-spending` endpoint? Does it trigger an on-chain transaction signed by the user's key, or is it an API call to the `proxy-router`? What parameters are required besides the amount?
4.  **Private Key Scope:** Is a single private key per user sufficient, or do users need to manage multiple keys? The current plan assumes one key per user.
5.  **Rate Limiting:** Are there specific rate-limiting requirements per user, per API key, or globally? (Consider libraries like `slowapi`).
6.  **Original Placeholders:** Confirm if the features marked "(placeholder)" in the original README (user registration, login, key management, session endpoints) need full implementation in this FastAPI version (plan assumes yes).
7.  **Security Requirements:** Are there any specific compliance or advanced security requirements beyond the best practices outlined (e.g., specific encryption algorithms, audit logging levels)?

## 9. Implementation Steps

This outlines a prioritized approach. Steps dependent on answers to "Open Questions" (Section 8) are placed later.

1.  **Project Setup & Core:**
    *   Initialize Python project (`pyproject.toml` or `requirements.txt`).
    *   Set up the basic FastAPI application structure (`src/main.py`) as outlined in Section 3.
    *   Implement configuration loading using Pydantic Settings (`src/core/config.py`).
    *   Create `Dockerfile` and `docker-compose.yml` for FastAPI app, PostgreSQL, and Redis (including Redis password config).
    *   Ensure basic application runs within Docker.
2.  **Database Setup:**
    *   Implement database connection logic (`src/db/database.py`).
    *   Define SQLAlchemy models (`src/db/models.py`) for `User`, `APIKey`, `UserPrivateKey`.
    *   Set up Alembic for migrations (`alembic/`, `alembic.ini`). Generate initial migration.
    *   Ensure migrations run correctly via Docker Compose.
3.  **User Authentication & JWT:**
    *   Implement password hashing and verification (`src/core/security.py`).
    *   Implement JWT generation and decoding (`src/core/security.py`).
    *   Create Pydantic schemas for User registration/login and Tokens (`src/schemas/user.py`, `src/schemas/token.py`).
    *   Implement CRUD operations for Users (`src/crud/user.py`).
    *   Implement `/auth/register` and `/auth/login` endpoints (`src/api/v1/auth.py`).
    *   Implement FastAPI dependency (`get_current_user` in `src/dependencies.py`) to validate JWT and retrieve user.
4.  **API Key Management:**
    *   Create Pydantic schemas for API Keys (`src/schemas/api_key.py`).
    *   Implement CRUD operations for API Keys, linking them to Users (`src/crud/api_key.py`). Remember to hash keys or store only metadata if possible.
    *   Implement `/auth/keys` endpoints (POST, GET, DELETE) protected by `get_current_user` (`src/api/v1/auth.py`).
    *   Implement `APIKeyHeader` security dependency (`src/core/security.py`, `src/dependencies.py`) to authenticate requests based on the `Authorization: Bearer <api_key>` header.
5.  **Private Key Storage & Encryption (Initial):**
    *   Create Pydantic schemas for Private Keys (`src/schemas/private_key.py`).
    *   Implement basic encryption/decryption logic using `cryptography` (`src/core/key_vault.py`). Initially, the master key can be sourced from env variables for local dev, clearly marked for replacement by KMS.
    *   Implement CRUD operations for storing/retrieving *encrypted* private keys associated with users (`src/crud/private_key.py` or integrated into `crud/user.py`).
    *   Implement `/auth/private-key` endpoints (POST, GET status, DELETE) protected by `get_current_user` (`src/api/v1/auth.py`).
6.  **OpenAI Endpoint Structure & Auth:**
    *   Define Pydantic schemas for OpenAI models and chat completions (`src/schemas/openai.py`).
    *   Create basic endpoint structure for `/v1/models` and `/v1/chat/completions` (`src/api/v1/models.py`, `src/api/v1/chat.py`).
    *   Apply the API Key authentication dependency to these endpoints. Ensure only valid API key holders can reach them. Return placeholder/mock data initially.
7.  **Redis Client & Basic Caching:**
    *   Implement Redis connection logic (`src/services/redis_client.py`) using configuration from `core/config.py` (including password).
    *   Implement basic caching logic (e.g., for placeholder model info) to test Redis integration.
8.  **KMS Integration:**
    *   Refine `src/core/key_vault.py` to integrate with the chosen KMS (AWS KMS, Vault, etc.) for secure master key handling. Replace environment variable sourcing for the master key.
9.  **-- AWAITING CLARIFICATION --**
    *   **(Depends on Q8.1, Q8.5):** Implement `src/services/proxy_router.py` with actual logic to communicate with the `proxy-router` based on its API specifics (auth, request structure, streaming). Potentially add rate limiting (`slowapi`).
    *   **(Depends on Q8.2):** Implement `src/services/model_mapper.py` to fetch/cache model mappings from the confirmed source. Update `/v1/models` endpoint logic.
    *   **(Depends on Q8.1):** Update `/v1/chat/completions` to:
        *   Use `model_mapper.py`.
        *   Retrieve/decrypt user private key using `key_vault.py`.
        *   Call `proxy_router.py` to forward the request.
        *   Handle responses (including streaming).
    *   **(Depends on Q8.1, Q8.6):** Implement `src/services/session_manager.py` *if* explicit session management with the proxy-router is required. Implement any related placeholder endpoints if confirmed necessary.
    *   **(Depends on Q8.3):** Implement `POST /auth/approve-spending` endpoint based on the clarified mechanism (blockchain tx or API call).
10. **Error Handling & Refinements:**
    *   Implement comprehensive FastAPI exception handlers (`src/main.py` or dedicated module) for cleaner error responses.
    *   Refine logging throughout the application.
    *   Update `README.md` with setup and usage instructions for the FastAPI version.
    *   Final code cleanup and review. 