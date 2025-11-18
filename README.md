# Morpheus API Gateway - FastAPI Implementation

A robust API Gateway connecting Web2 clients to the Morpheus-Lumerin AI Marketplace using FastAPI, PostgreSQL, and secure key management practices.

## Overview

This project migrates the existing Morpheus API Gateway functionality from Node.js/Express to Python/FastAPI, incorporating robust authentication, persistent storage, secure key management, and best practices for API development and deployment using Docker.

The gateway provides OpenAI-compatible endpoints that connect to the Morpheus blockchain, allowing users to access AI models in a familiar way while leveraging blockchain technology behind the scenes.

## Technology Stack

- **Web Framework:** FastAPI
- **Data Validation:** Pydantic
- **Database ORM:** SQLAlchemy with Alembic for migrations
- **Database:** PostgreSQL
- **Caching:** In-memory with DirectModelService
- **Asynchronous HTTP Client:** `httpx` (for communicating with the proxy-router)
- **JWT Handling:** `python-jose`
- **Password Hashing:** `passlib[bcrypt]`
- **Cryptography:** `cryptography` for private key encryption
- **KMS Integration:** AWS KMS for secure key management
- **Containerization:** Docker, Docker Compose

## Project Structure

```
morpheus_api_python/
├── alembic/                  # Database migrations
├── alembic.ini
├── src/
│   ├── api/                  # FastAPI routers/endpoints
│   │   ├── v1/
│   │   │   ├── auth.py       # User registration, login, API keys, private key mgmt
│   │   │   ├── models.py     # OpenAI compatible models endpoint
│   │   │   ├── chat.py       # OpenAI compatible chat completions
│   │   │   ├── chat_history.py # Chat history management
│   │   │   ├── session.py    # Session management
│   │   │   └── automation.py # Automation settings
│   │   └── __init__.py
│   ├── core/                 # Core logic, configuration, security
│   │   ├── config.py         # Pydantic settings
│   │   ├── security.py       # JWT generation/validation, password hashing, API key handling
│   │   ├── key_vault.py      # Private key encryption/decryption, KMS interaction
│   │   ├── direct_model_service.py # Direct model fetching with in-memory cache
│   │   ├── model_routing.py  # Model name to blockchain ID routing
│   │   ├── local_testing.py  # Local testing mode and authentication bypass
│   │   └── __init__.py
│   ├── crud/                 # Database interaction functions
│   │   ├── user.py
│   │   ├── api_key.py
│   │   ├── private_key.py
│   │   ├── chat.py           # Chat and message CRUD operations
│   │   ├── session.py        # Session CRUD operations
│   │   └── __init__.py
│   ├── db/                   # Database session management, base model
│   │   ├── database.py
│   │   ├── models.py         # SQLAlchemy models (includes Chat, Message)
│   │   └── __init__.py
│   ├── schemas/              # Pydantic schemas for request/response validation
│   │   ├── user.py
│   │   ├── token.py
│   │   ├── api_key.py
│   │   ├── private_key.py
│   │   ├── openai.py         # Schemas for OpenAI compatibility
│   │   └── __init__.py
│   ├── services/             # Business logic layer
│   │   ├── cognito_service.py    # Cognito integration
│   │   ├── session_service.py    # Session management logic
│   │   ├── proxy_router.py       # Proxy router communication
│   │   └── __init__.py
│   ├── dependencies.py       # FastAPI dependency injection functions
│   └── main.py               # FastAPI application instance and root setup
├── ai-docs/                  # Documentation and guides
│   ├── DOCKER_TESTING.md     # Local testing guide
│   ├── SELF_CONTAINED_LOCAL_TESTING.md # Detailed local setup
│   └── ...                   # Other documentation files
├── scripts/                  # Development and deployment scripts
│   ├── test_local.sh         # Self-contained local testing
│   ├── start_local_dev.sh    # Container startup with auto-migration
│   └── ...                   # Other utility scripts
├── tests/                    # Test files
│   ├── api/                  # API integration tests
│   └── unit/                 # Unit tests
├── .env.example              # Production environment template
├── env.local.example         # Local testing environment template
├── .gitignore                # Git ignore file
├── Dockerfile                # Docker build configuration
├── docker-compose.local.yml  # Local development container setup
├── pyproject.toml            # Python project dependencies
└── README.md                 # This file
```

## Getting Started

### 🚀 Quick Start - Local Development (Recommended)

**No external dependencies needed!** For rapid development and testing:

```bash
# One-time setup
cp env.local.example .env.local

# Start local development environment
./scripts/test_local.sh
```

**What this gives you:**
- ✅ **No Authentication Required** - All endpoints work without tokens/keys
- ✅ **Fresh Database** - PostgreSQL with auto-migrations on every start
- ✅ **Hot Reload** - Code changes apply instantly
- ✅ **Complete API Testing** - Test all endpoints via Swagger UI
- ✅ **Ephemeral Environment** - Clean state every restart

**Access your local environment:**
- **API**: http://localhost:8000
- **Swagger UI**: http://localhost:8000/docs (all endpoints work without auth!)
- **Health Check**: http://localhost:8000/health

📚 **For detailed testing instructions, see [Docker Testing Guide](ai-docs/DOCKER_TESTING.md)**

### Prerequisites (Production Setup)

- Python 3.11+
- Docker and Docker Compose
- PostgreSQL (if running locally without Docker)
- AWS Account with KMS access (for production)

### Production Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/morpheus-api-python.git
   cd morpheus-api-python
   ```

2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install poetry
   poetry install
   ```

4. Configure environment variables:
   ```bash
   cp .env.example .env
   ```
   Edit the `.env` file with your specific settings

### Environment Configuration

Configure the following key environment variables:

```
# Database
POSTGRES_USER=morpheus_user
POSTGRES_PASSWORD=secure_password_here
POSTGRES_DB=morpheus_db
DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432/${POSTGRES_DB}

# Model Service (uses in-memory caching)
ACTIVE_MODELS_URL=https://active.dev.mor.org/active_models.json
DEFAULT_FALLBACK_MODEL=mistral-31-24b
DEFAULT_FALLBACK_EMBEDDINGS_MODEL=text-embedding-bge-m3
DEFAULT_FALLBACK_TTS_MODEL=tts-kokoro
DEFAULT_FALLBACK_STT_MODEL=whisper-1

# JWT
JWT_SECRET_KEY=generate_this_with_openssl_rand_-hex_32
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# Logging
LOG_LEVEL=INFO                        # Master log level: DEBUG, INFO, WARNING, ERROR (controls all components)
LOG_JSON=true                         # Enable JSON structured logging (recommended for production)
LOG_IS_PROD=false                     # Production mode logging (affects performance)

# Example: Enable verbose logging for specific components during development
# LOG_LEVEL_PROXY=DEBUG        # Debug session creation/proxy communication
# LOG_LEVEL_MODELS=DEBUG       # Debug model fetching and caching
# LOG_LEVEL_AUTH=WARN          # Reduce auth noise to warnings only
# LOG_LEVEL_API=DEBUG          # Debug chat completion and API endpoints
# LOG_LEVEL_CORE=WARN          # Reduce infrastructure noise

### **📊 Log Format**

**JSON Format (LOG_JSON=true):**
```json
 {
   "component": "MODELS", 
   "cache_expires_in_seconds": 295.20263, 
   "event_type": "cache_hit", 
   "event": "Using cached model data",
   "level": "debug", 
   "timestamp": "2025-10-01T10:04:58.493721Z", 
   "logger": "DEBUG", 
   "caller": "direct_model_service.py:107"
}
```

**Console Format (LOG_JSON=false):**
```
2025-10-01T09:35:58.366411Z [debug] Using cached model data        [DEBUG] cache_expires_in_seconds=269.904167 component=MODELS event_type=cache_hit
```

### **🚀 Production Recommendations**

```bash
# Production settings
LOG_LEVEL=INFO               # Master control
LOG_JSON=true
LOG_IS_PROD=true

# Reduce noise from infrastructure
LOG_LEVEL_CORE=WARN          # Reduce HTTP/FastAPI noise
LOG_LEVEL_AUTH=WARN          # Only auth errors

# Monitor critical business logic
LOG_LEVEL_PROXY=INFO         # Monitor proxy communication
LOG_LEVEL_API=INFO           # Monitor API endpoints
LOG_LEVEL_MODELS=INFO        # Monitor model service
LOG_LEVEL_DATABASE=ERROR     # Only database errors
```

### **🛠️ Development/Debugging**

```bash
# Development settings
LOG_LEVEL=DEBUG              # Master control
LOG_JSON=false
LOG_IS_PROD=false

# Debug specific functional areas
LOG_LEVEL_PROXY=DEBUG        # Verbose session/proxy debugging
LOG_LEVEL_MODELS=DEBUG       # Model fetching and caching
LOG_LEVEL_API=DEBUG          # Chat completion and API flow
LOG_LEVEL_CORE=WARN          # Reduce infrastructure noise
```

# AWS KMS (for production)
KMS_PROVIDER=aws
KMS_MASTER_KEY_ID=your_kms_key_id_or_arn
AWS_REGION=us-east-1
# AWS_ACCESS_KEY_ID=your_access_key_id         # If not using IAM roles
# AWS_SECRET_ACCESS_KEY=your_secret_access_key # If not using IAM roles

# Development mode local encryption (not for production)
MASTER_ENCRYPTION_KEY=generate_this_with_openssl_rand_-hex_32

# Proxy Router
PROXY_ROUTER_URL=http://localhost:8545  # URL of the Morpheus-Lumerin Node proxy-router
```

## Database Setup

### Local Development Database

**Using Local Testing Environment (Recommended):**
- Database setup is **automatic** when using `./scripts/test_local.sh`
- PostgreSQL starts in Docker with auto-migrations
- No manual database setup required!

### Manual Database Setup (Production)

1. Start PostgreSQL:
   ```bash
   docker run --name morpheus-postgres -e POSTGRES_USER=morpheus_user -e POSTGRES_PASSWORD=morpheus_password -e POSTGRES_DB=morpheus_db -p 5432:5432 -d postgres:15-alpine
   ```

2. Run migrations:
   ```bash
   alembic upgrade head
   ```

### Database Migrations

**Local Development:**
- Migrations run automatically on container startup
- Always uses the latest schema
- Fresh database on every restart

**Production/Manual:**

Generate a new migration after model changes:
```bash
alembic revision --autogenerate -m "Description of changes"
```

Apply migrations:
```bash
alembic upgrade head
```

Roll back migrations:
```bash
alembic downgrade -1  # Roll back one migration
```

## Model Service

The API uses an in-memory caching system for model data fetched from CloudFront. No external caching service is required.

## AWS KMS Setup (Production)

1. Create a KMS key in the AWS console or using AWS CLI
2. Note the key ARN or ID
3. Configure IAM permissions for your service principal
4. Update environment variables with key details

## Running the Application

### 🚀 Local Development (Recommended)

**One command starts everything:**
```bash
./scripts/test_local.sh
```

**Features:**
- ✅ PostgreSQL database with auto-migrations
- ✅ API with hot reload
- ✅ Authentication bypass for easy testing
- ✅ All endpoints accessible via Swagger UI

**Monitor your environment:**
```bash
# View logs
docker compose -f docker-compose.local.yml logs -f api-local

# Check status
docker compose -f docker-compose.local.yml ps

# Stop environment
docker compose -f docker-compose.local.yml down
```

### Production Docker Setup

1. Build and start containers:
   ```bash
   docker-compose up -d
   ```

2. Check container status:
   ```bash
   docker-compose ps
   ```

3. View logs:
   ```bash
   docker-compose logs -f api
   ```

### Manual Local Development

1. Start PostgreSQL (see Database Setup above)

2. Run the FastAPI application:
   ```bash
   uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
   ```

## Testing the API

### 🧪 Local Development Testing

**Using Local Environment (No Authentication Required):**
```bash
# Start environment
./scripts/test_local.sh

# Access Swagger UI - all endpoints work without authentication!
# http://localhost:8000/docs
```

**Test any endpoint directly:**
- No Bearer tokens needed
- No API keys required
- Click "Try it out" and execute immediately
- Test user automatically created: `test@local.dev`

### API Documentation

FastAPI automatically generates interactive API documentation:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

📚 **For comprehensive testing instructions, see [Docker Testing Guide](ai-docs/DOCKER_TESTING.md)**

### Core Endpoints

#### Authentication

- `POST /api/v1/auth/register` - Create a new user
- `POST /api/v1/auth/login` - Log in and get JWT tokens
- `POST /api/v1/auth/refresh` - Refresh access token

#### API Key Management

- `POST /api/v1/auth/keys` - Create a new API key
- `GET /api/v1/auth/keys` - List your API keys
- `DELETE /api/v1/auth/keys/{key_id}` - Delete an API key

#### Private Key Management

- `POST /api/v1/auth/private-key` - Store your blockchain private key
- `GET /api/v1/auth/private-key/status` - Check if you have a stored private key
- `DELETE /api/v1/auth/private-key` - Delete your stored private key

#### OpenAI-Compatible Endpoints

- `GET /api/v1/models` - List available models
- `GET /api/v1/models/{model_id}` - Get model details
- `POST /api/v1/chat/completions` - Create a chat completion

### Example: Testing Chat Completion

#### 🚀 Local Development (No Authentication)

```bash
# Start local environment
./scripts/test_local.sh

# Test chat completion directly (no auth needed!)
curl -X POST http://localhost:8000/api/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [
      {"role": "user", "content": "Hello, how are you?"}
    ]
  }'

# Test chat history
curl -X POST http://localhost:8000/api/v1/chat-history/chats \
  -H "Content-Type: application/json" \
  -d '{"title": "Test Chat"}'

# List available models
curl http://localhost:8000/api/v1/models
```

#### 🏭 Production (Full Authentication Flow)

1. Register a user:
   ```bash
   curl -X POST http://localhost:8000/api/v1/auth/register \
     -H "Content-Type: application/json" \
     -d '{"name": "Test User", "email": "user@example.com", "password": "securepassword"}'
   ```

2. Login to get tokens:
   ```bash
   curl -X POST http://localhost:8000/api/v1/auth/login \
     -H "Content-Type: application/json" \
     -d '{"email": "user@example.com", "password": "securepassword"}'
   ```

3. Create an API key (using JWT from login):
   ```bash
   curl -X POST http://localhost:8000/api/v1/auth/keys \
     -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name": "My API Key"}'
   ```

4. Store a private key:
   ```bash
   curl -X POST http://localhost:8000/api/v1/auth/private-key \
     -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"private_key": "YOUR_BLOCKCHAIN_PRIVATE_KEY"}'
   ```

5. Create a chat completion using the API key:
   ```bash
   curl -X POST http://localhost:8000/api/v1/chat/completions \
     -H "Authorization: Bearer YOUR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "model": "gpt-3.5-turbo",
       "messages": [
         {"role": "user", "content": "Hello, how are you?"}
       ]
     }'
   ```

## Health Checks

- `GET /health` - Check API and model service health
- `GET /` - Basic API information

## Open Questions and TODOs

This implementation is based on the [FastAPI Implementation Plan](fastapi_implementation_plan.md) and has the following open questions:

1. **Proxy-Router API Specifics:** The exact API contract of the `morpheus-lumerin-node` `proxy-router` needs clarification.
2. **Model Mapping Source:** The source of mapping between OpenAI model names and blockchain model IDs needs to be determined.
3. **Token Spending Approval:** The mechanism for the `/auth/approve-spending` endpoint needs specification.
4. **Private Key Scope:** Confirm if a single private key per user is sufficient.
5. **Rate Limiting:** Determine rate-limiting requirements and implement if needed.
6. **Security Requirements:** Confirm if there are any specific compliance or advanced security requirements.

The current implementation uses placeholder/mock data for model information and chat completions until the proxy-router integration is finalized.

## 🔄 Development Workflow

### Daily Development
```bash
# Start local environment (one-time per session)
./scripts/test_local.sh

# Make code changes (hot reload active - changes apply instantly!)
# Test in Swagger UI: http://localhost:8000/docs

# Stop environment when done
# Ctrl+C or: docker compose -f docker-compose.local.yml down
```

### Key Features for Development
- ✅ **Hot Reload** - Code changes apply instantly
- ✅ **No Authentication** - Test all endpoints immediately
- ✅ **Fresh Database** - Clean state every restart
- ✅ **Auto-Migration** - Database schema always current
- ✅ **Comprehensive Testing** - All endpoints accessible via Swagger UI

### Testing Different Scenarios
```bash
# Test chat functionality
curl -X POST http://localhost:8000/api/v1/chat-history/chats \
  -H "Content-Type: application/json" \
  -d '{"title": "Development Test"}'

# Test model service
curl http://localhost:8000/api/v1/models

# Test health checks
curl http://localhost:8000/health
```

## Load Testing

A comprehensive load testing tool is available to test API performance and database operations:

```bash
# Install dependencies
pip install -r load_test_requirements.txt

# Run load test
python load_test.py \
  --url https://api.morpheus.example.com \
  --bearer-token "eyJ..." \
  --users 50 \
  --duration 120
```

The load testing script focuses on endpoints that don't depend on the proxy-router:
- ✅ Authentication and API key management
- ✅ Chat history CRUD operations
- ✅ Automation settings
- ✅ Model listing and health checks
- ❌ Session management (requires proxy-router)
- ❌ Chat completions (requires proxy-router)
- ❌ Embeddings (requires proxy-router)

**Features:**
- Realistic user behavior simulation
- Detailed performance metrics (p50, p95, p99)
- JSON and console output
- Concurrent user support (configurable)
- Progress tracking

**See [LOAD_TEST_README.md](LOAD_TEST_README.md) for complete documentation.**

## Development and Contributing

- Format code with `ruff format`
- Run linting with `ruff check`
- Run type checking with `mypy`
- Run tests with `pytest`

## License

[MIT License](LICENSE) 