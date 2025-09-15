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
│   │   │   └── chat.py       # OpenAI compatible chat completions
│   │   └── __init__.py
│   ├── core/                 # Core logic, configuration, security
│   │   ├── config.py         # Pydantic settings
│   │   ├── security.py       # JWT generation/validation, password hashing, API key handling
│   │   ├── key_vault.py      # Private key encryption/decryption, KMS interaction
│   │   └── __init__.py
│   ├── crud/                 # Database interaction functions
│   │   ├── user.py
│   │   ├── api_key.py
│   │   ├── private_key.py
│   │   └── __init__.py
│   ├── db/                   # Database session management, base model
│   │   ├── database.py
│   │   ├── models.py         # SQLAlchemy models
│   │   └── __init__.py
│   ├── schemas/              # Pydantic schemas for request/response validation
│   │   ├── user.py
│   │   ├── token.py
│   │   ├── api_key.py
│   │   ├── private_key.py
│   │   ├── openai.py         # Schemas for OpenAI compatibility
│   │   └── __init__.py
│   ├── services/             # Business logic layer
│   │   ├── direct_model_service.py   # In-memory model caching
│   │   ├── model_mapper.py   # Mapping OpenAI model names <-> Blockchain IDs
│   │   ├── init_cache.py     # Cache initialization
│   │   └── __init__.py
│   ├── dependencies.py       # FastAPI dependency injection functions
│   └── main.py               # FastAPI application instance and root setup
├── .env.example              # Environment variable template
├── .gitignore                # Git ignore file
├── Dockerfile                # Docker build configuration
├── docker-compose.yml        # Container orchestration configuration
├── pyproject.toml            # Python project dependencies
└── README.md                 # This file
```

## Getting Started

### Prerequisites

- Python 3.11+
- Docker and Docker Compose
- PostgreSQL (if running locally)
- AWS Account with KMS access (for production)

### Installation

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

# JWT
JWT_SECRET_KEY=generate_this_with_openssl_rand_-hex_32
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

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

### Local Database Setup

1. Start PostgreSQL:
   ```bash
   docker run --name morpheus-postgres -e POSTGRES_USER=morpheus_user -e POSTGRES_PASSWORD=morpheus_password -e POSTGRES_DB=morpheus_db -p 5432:5432 -d postgres:15-alpine
   ```

2. Run migrations:
   ```bash
   alembic upgrade head
   ```

### Running Migrations

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

### Docker Setup (Recommended)

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

### Local Development

1. Start PostgreSQL (see above)

2. Run the FastAPI application:
   ```bash
   uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
   ```

## Testing the API

### API Documentation

FastAPI automatically generates interactive API documentation:

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### Core uvicorn src.main:app --reload --host 0.0.0.0 --port 8000Endpoints

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

### Example: Creating a Chat Completion

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
     -d '{}'
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

## Development and Contributing

- Format code with `ruff format`
- Run linting with `ruff check`
- Run type checking with `mypy`
- Run tests with `pytest`

## License

[MIT License](LICENSE) 