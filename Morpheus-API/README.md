# Morpheus API Gateway

The Morpheus API Gateway is a middleware service that connects Web2 clients to the Morpheus-Lumerin blockchain-based AI marketplace. It provides an OpenAI-compatible REST API while abstracting away the complexities of blockchain interactions, session management, and protocol translations.

## Core Functionality

The gateway handles these key responsibilities:

1. **OpenAI API Compatibility** - Provides a drop-in replacement for OpenAI's API, allowing existing applications to integrate with minimal changes
2. **Session Management** - Creates and manages blockchain sessions for AI model inference
3. **Model Mapping** - Translates between OpenAI model names and blockchain model IDs
4. **User Authentication** - Handles API key-based authentication for users
5. **Request Proxying** - Forwards requests to the underlying proxy-router with appropriate transformations
6. **Private Key Management** - Securely stores and manages user private keys for blockchain transactions

## Architecture

The current implementation consists of these components:

### API Layer
- Express.js server with middleware for authentication, rate limiting, and error handling
- OpenAI-compatible endpoints for model listing and chat completions
- Custom endpoints for session management

### Service Layer
- **SessionService**: Handles blockchain session lifecycle (creation, interaction, closure)
- **ModelMappingService**: Maps between OpenAI model names and blockchain model IDs
- **UserService**: Manages user accounts and API keys
- **KeyVaultService**: Securely stores and manages user private keys
- **ProxyRouterClient**: Handles interactions with the Morpheus-Lumerin-Node proxy-router
- **RedisClient**: Provides caching for sessions, models, and other data

### Integration Layer
- Communication with the proxy-router service
- Translation between OpenAI API format and internal formats

### Frontend
- Simple web interface for users to learn about the API
- Interactive Swagger UI documentation for testing API endpoints
- Code examples for popular programming languages

## Key Rotation Architecture

The API Gateway uses a "Key Rotation" approach to handle multiple user private keys with a single proxy-router instance:

1. **Private Key Storage**: User private keys are encrypted and stored in Redis with the API key as a mapping key
2. **Session-based Key Selection**: When a user makes a request, their API key is used to retrieve their private key
3. **Dynamic Auth**: The private key is used to authenticate with the proxy-router for that specific request
4. **Request Isolation**: Each request is isolated to ensure one user's request doesn't affect another's
5. **Memory Cache**: Active keys are cached in memory for better performance while maintaining security

This approach provides several benefits:
- **Security isolation**: Each user's funds are kept separate
- **Single proxy router**: Simplifies infrastructure requirements
- **Transparent to users**: Users interact with the OpenAI-compatible API without dealing with blockchain complexity
- **Scalable design**: Additional proxy-router instances can be added as needed

## How It Works

### Initialization Flow
1. The Express application starts and loads environment configuration
2. Redis connection is established for session and model caching
3. Model mapping service initializes and caches available models
4. API routes are registered for authentication and model interactions

### User Onboarding Flow
1. User creates an account and gets an API key
   ```bash
   # Register a new user - this also generates an initial API key
   curl -X POST http://localhost:3000/auth/register \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Your Name",
       "email": "your.email@example.com",
       "password": "yourpassword"
     }'
   
   # Expected response:
   # {
   #   "user": {
   #     "id": "user_...",
   #     "name": "Your Name",
   #     "email": "your.email@example.com",
   #     "createdAt": "2023-...",
   #     "updatedAt": "2023-..."
   #   },
   #   "api_key": "sk-..."
   # }
   
   # Generate additional API keys if needed (requires authentication)
   curl -X POST http://localhost:3000/auth/keys \
     -H "Authorization: Bearer sk-your-api-key" \
     -H "Content-Type: application/json"
   
   # List your API keys
   curl -X GET http://localhost:3000/auth/keys \
     -H "Authorization: Bearer sk-your-api-key"
   ```

2. User registers their private key with the API Gateway
   ```bash
   # Register your blockchain private key
   curl -X POST http://localhost:3000/auth/private-key \
     -H "Authorization: Bearer sk-your-api-key" \
     -H "Content-Type: application/json" \
     -d '{
       "privateKey": "your-ethereum-private-key-here"
     }'
   ```

3. API Gateway securely stores the encrypted private key

4. User approves the proxy-router contract to spend their MOR tokens
   ```bash
   # Approve token spending
   curl -X POST http://localhost:3000/auth/approve-spending \
     -H "Authorization: Bearer sk-your-api-key" \
     -H "Content-Type: application/json" \
     -d '{
       "amount": 3
     }'
   ```

Once these steps are completed, you can start using the AI inference endpoints with your API key.

### Request Processing Flow

#### Chat Completion Request
1. Request arrives at `/v1/chat/completions` endpoint
2. Authentication middleware validates the API key
3. The request is parsed and validated
4. The user's private key is retrieved from the secure vault
5. The OpenAI model name is mapped to a blockchain model ID
6. The system checks for an existing active session or creates a new one using the user's private key
7. The request is forwarded to the proxy-router with the session ID and private key
8. For streaming responses, chunks are processed and forwarded to the client
9. For non-streaming responses, the complete response is returned

#### Session Management
- Sessions are created on-demand when needed for a particular model
- Active sessions are cached in Redis with user associations
- The system reuses existing sessions when available
- Sessions are automatically closed when they expire

### Model Mapping System
The service translates between familiar OpenAI model names and blockchain model IDs:
- Default mappings provide fallbacks for common models
- Periodic cache refreshing ensures up-to-date model information
- Custom model name mapping handles differences in naming conventions

## API Endpoints

### OpenAI-Compatible Endpoints
- `GET /v1/models` - List available models in OpenAI format
- `POST /v1/chat/completions` - Generate a chat completion

### Authentication Endpoints
- `POST /auth/register` - Create a new user account (placeholder)
- `POST /auth/login` - Authenticate user and generate tokens (placeholder)
- `GET /auth/keys` - List API keys for the authenticated user (placeholder)
- `POST /auth/keys` - Generate a new API key (placeholder)
- `DELETE /auth/keys/{key_id}` - Revoke an API key (placeholder)
- `POST /auth/private-key` - Register a private key for the current API key
- `GET /auth/private-key/status` - Check if a private key exists for the current API key
- `DELETE /auth/private-key` - Delete the private key for the current API key
- `POST /auth/approve-spending` - Approve the contract to spend MOR tokens

### Session Management Endpoints
- `GET /v1/sessions` - List active sessions (placeholder)
- `POST /v1/sessions` - Create a new session (placeholder)
- `DELETE /v1/sessions/{session_id}` - Close a session (placeholder)

## Technical Implementation

### Environment Configuration
The system uses environment variables for configuration, including:
- Server settings (port, CORS, etc.)
- Database connections (PostgreSQL and Redis)
- Blockchain settings (Ethereum RPC, contract addresses)
- Proxy router connection details
- Authentication settings
- Key vault encryption settings

### Security Considerations
- Private keys are encrypted at rest using AES-256-CBC
- All requests are authenticated with API keys
- HTTPS should be enabled in production
- Rate limiting protects against abuse
- Redis should be configured with password authentication in production
- The encryption key should be stored in a secure key management system in production

### Caching Strategy
- Redis is used for caching session information and model mappings
- Sessions are cached with expiration based on their blockchain duration
- Model information is refreshed periodically to stay in sync

### Error Handling
- Standardized error response format compatible with OpenAI
- Custom error classes for different error types
- Comprehensive logging with Winston

### Authentication
- API key-based authentication using the Authorization header
- Currently uses an in-memory store (to be replaced with database)
- JWT infrastructure in place for future user authentication

## Interactive Documentation

The API Gateway comes with built-in Swagger UI documentation, allowing developers to:

1. Explore available endpoints and their parameters
2. Test API calls directly from the browser
3. View request and response schemas
4. Try out authentication and session management

The documentation is accessible at:
- `/api-docs` - Swagger UI interface
- `/` - Main landing page with embedded Swagger UI

The OpenAPI specification is defined in `swagger.json` at the root of the project.

## Development & Deployment

### Local Development
```bash
# Clone the repository
git clone [repository-url]

# Install dependencies
npm install

# Set up environment variables
cp .env.example .env
# Edit .env with your configuration

# Start development server
npm run dev
```

### Docker Deployment
```bash
# Build the Docker image
docker build -t morpheus-api .

# Run with Docker
docker run -p 3000:3000 --env-file .env morpheus-api

# Or use docker-compose
docker-compose up
```

## Testing

The current implementation includes a test API key for easy integration testing:
- API Key: `test-api-key`
- User ID: `test-user-id`

Example API request:
```bash
curl -X POST http://localhost:3000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-api-key" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello, world!"}]
  }'
```

Example API request to register a private key:
```bash
curl -X POST http://localhost:3000/auth/private-key \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-api-key" \
  -d '{
    "privateKey": "YOUR_PRIVATE_KEY"
  }'
```

Example API request to approve token spending:
```bash
curl -X POST http://localhost:3000/auth/approve-spending \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-api-key" \
  -d '{
    "amount": 3
  }'
```

Example API request for chat completion:
```bash
curl -X POST http://localhost:3000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-api-key" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello, world!"}]
  }'
``` 