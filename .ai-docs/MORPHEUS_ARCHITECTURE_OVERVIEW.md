# Morpheus Marketplace Architecture Overview

## System Architecture Diagram

```
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 MORPHEUS MARKETPLACE ECOSYSTEM                                │
└───────────────────────────────────────────────────────────────────────────────────────────────┘

                                      USER INTERFACES
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                               │
│  ┌───────────────────────┐                        ┌───────────────────────┐                   │
│  │   OpenBeta Website    │                        │  Active Models Site   │                   │
│  │   (Documentation)     │                        │  (Web Application)    │                   │
│  │                       │                        │                       │                   │
│  │  api.mor.org          │                        │  app.mor.org          │                   │
│  │  Next.js + Docs       │                        │  Next.js + Chat UI    │                   │
│  └───────────┬───────────┘                        └───────────┬───────────┘                   │
│              │                                                │                               │
│              │                   ┌─────────────┐              │                               │
│              └──────────────────▶│  Human User │◀─────────────┘                               │
│                                  └─────────────┘                                              │
│                                                                                               │
│  ┌────────────────────────────────────────────────────────────────────────┐                   │
│  │                          Agent/Bot Applications                        │                   │
│  │  (Direct API Integration via OpenAI-compatible endpoints)              │                   │
│  └────────────────────────────────────────────────────────────────────────┘                   │
│              │                                                │                               │
└──────────────┼────────────────────────────────────────────────┼───────────────────────────────┘
               │                                                                                │
               │                 AWS COGNITO                                                    │
               │            (Authentication Layer)                                              │
               │       ┌──────────────────────────┐                                             │
               └──────▶│   OAuth2 / OpenID        │◀────────────────────────────────────────────┘
                       │   User Pool + Client ID                                                │
                       │   auth.mor.org                                                         │
                       └────────────┬───────────────────────────────────────────────────────────┘
                                                                                                │
                                    │ JWT Tokens
                                    ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│                          API GATEWAY LAYER (C-Node Interface)                                 │
│  ┌───────────────────────────────────────────────────────────────────────────────────────┐    │
│  │                    Morpheus Marketplace API Gateway                                   │    │
│  │                        (FastAPI + PostgreSQL RDS)                                     │    │
│  │                            api.mor.org                                                │    │
│  │                                                                                       │    │
│  │  ┌────────────────────────────────────────────────────────────────────────────────┐  │     │
│  │  │  Authentication & User Management                                              │  │     │
│  │  │  • JWT token validation (Cognito)                                              │  │     │
│  │  │  • API key generation & management                                             │  │     │
│  │  │  • User profile & settings                                                     │  │     │
│  │  │  • Private key encryption/storage                                              │  │     │
│  │  └────────────────────────────────────────────────────────────────────────────────┘  │     │
│  │                                                                                       │    │
│  │  ┌────────────────────────────────────────────────────────────────────────────────┐  │     │
│  │  │  OpenAI-Compatible Endpoints                                                   │  │     │
│  │  │  • POST /v1/chat/completions (streaming & non-streaming)                       │  │     │
│  │  │  • GET /v1/models (list available models)                                      │  │     │
│  │  │  • POST /v1/embeddings (text embeddings)                                       │  │     │
│  │  └────────────────────────────────────────────────────────────────────────────────┘  │     │
│  │                                                                                       │    │
│  │  ┌────────────────────────────────────────────────────────────────────────────────┐  │     │
│  │  │  Session Management & Automation                                               │  │     │
│  │  │  • Blockchain session creation (via C-Node proxy-router)                       │  │     │
│  │  │  • Automated session management per API key                                    │  │     │
│  │  │  • Session lifecycle tracking & cleanup                                        │  │     │
│  │  │  • Token approval & spending management                                        │  │     │
│  │  └────────────────────────────────────────────────────────────────────────────────┘  │     │
│  │                                                                                       │    │
│  │  ┌────────────────────────────────────────────────────────────────────────────────┐  │     │
│  │  │  Model Routing & Discovery                                                     │  │     │
│  │  │  • Model name → Blockchain ID mapping                                          │  │     │
│  │  │  • Active model synchronization (active.mor.org)                               │  │     │
│  │  │  • Automatic model fallback logic                                              │  │     │
│  │  │  • Model metadata & pricing info                                               │  │     │
│  │  └────────────────────────────────────────────────────────────────────────────────┘  │     │
│  │                                                                                       │    │
│  │  ┌────────────────────────────────────────────────────────────────────────────────┐  │     │
│  │  │  PostgreSQL RDS Database (Core System - 4 Tables)                              │  │     │
│  │  │  • users - User accounts & authentication                                      │  │     │
│  │  │  • api_keys - API key management (high traffic)                                │  │     │
│  │  │  • sessions - Active session tracking (highest traffic)                        │  │     │
│  │  │  • user_automation_settings - Session automation config                        │  │     │
│  │  └────────────────────────────────────────────────────────────────────────────────┘  │     │
│  │                                                                                       │    │
│  └───────────────────────────────────────────────────────────────────────────────────────┘    │
└────────────────────────────────────┬──────────────────────────────────────────────────────────┘
                                                                                                │
                                       │ HTTP/HTTPS
                                       │ (httpx async client)
                                       ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│                          CONSUMER NODE (C-Node) - Proxy Router                                │
│  ┌───────────────────────────────────────────────────────────────────────────────────────┐    │
│  │                   Morpheus-Lumerin-Node (proxy-router)                                │    │
│  │                                                                                       │    │
│  │  ┌────────────────────────────────────────────────────────────────────────────────┐  │     │
│  │  │  Blockchain Integration                                                        │  │     │
│  │  │  • Monitors Arbitrum blockchain (MainNet: 42161, TestNet: 421614)             │  │      │
│  │  │  • Diamond MarketPlace contract interaction                                   │  │      │
│  │  │  • MOR token transactions (staking, payments)                                 │  │      │
│  │  │  • ETH gas fee management                                                     │  │      │
│  │  │  • Session registration & validation                                          │  │      │
│  │  └────────────────────────────────────────────────────────────────────────────────┘  │     │
│  │                                                                                       │    │
│  │  ┌────────────────────────────────────────────────────────────────────────────────┐  │     │
│  │  │  Session & Request Routing                                                     │  │     │
│  │  │  • Secure session management between consumers & providers                    │  │      │
│  │  │  • Request/response routing (prompts → inference)                             │  │      │
│  │  │  • Session state tracking (idle timeout, capacity policy)                     │  │      │
│  │  │  • Chat context management (optional PROXY_STORE_CHAT_CONTEXT)                │  │      │
│  │  │  • Concurrent request handling per session                                    │  │      │
│  │  └────────────────────────────────────────────────────────────────────────────────┘  │     │
│  │                                                                                       │    │
│  │  ┌────────────────────────────────────────────────────────────────────────────────┐  │     │
│  │  │  Provider Discovery & Bidding                                                  │  │     │
│  │  │  • Active provider model discovery                                             │  │     │
│  │  │  • Bid evaluation & selection                                                  │  │     │
│  │  │  • Provider reputation & reliability tracking                                  │  │     │
│  │  │  • Price comparison & optimization                                             │  │     │
│  │  └────────────────────────────────────────────────────────────────────────────────┘  │     │
│  │                                                                                       │    │
│  └───────────────────────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────┬──────────────────────┬─────────────────────────────────────────┘
                               │                                                                │
                 ┌─────────────┴──────────┐  ┌────────┴─────────────────────────────────────────┐
                 │                        │  │                                                  │
                 ▼                        ▼  ▼                   ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│                       PROVIDER NODES (P-Nodes) - Multiple Providers                           │
│                                                                                               │
│  ┌──────────────────────────┐    ┌──────────────────────────┐    ┌──────────────────────┐     │
│  │  Provider Node #1        │    │  Provider Node #2        │    │  Provider Node #N    │     │
│  │  (Lumerin Node Instance) │    │  (Lumerin Node Instance) │    │  (Lumerin Node...)   │     │
│  │                          │    │                          │    │                      │     │
│  │  ┌────────────────────┐  │    │  ┌────────────────────┐  │    │  ┌────────────────┐ │      │
│  │  │ Blockchain Wallet  │  │    │  │ Blockchain Wallet  │  │    │  │ Blockchain ... │ │      │
│  │  │ • MOR staking      │  │    │  │ • MOR staking      │  │    │  │ • MOR staking  │ │      │
│  │  │ • Bid management   │  │    │  │ • Bid management   │  │    │  │ • Bid manage.. │ │      │
│  │  │ • Payment receipt  │  │    │  │ • Payment receipt  │  │    │  │ • Payment ...  │ │      │
│  │  └────────────────────┘  │    │  └────────────────────┘  │    │  └────────────────┘ │      │
│  │                          │    │                          │    │                      │     │
│  │  ┌────────────────────┐  │    │  ┌────────────────────┐  │    │  ┌────────────────┐ │      │
│  │  │ Model Registry     │  │    │  │ Model Registry     │  │    │  │ Model Registry │ │      │
│  │  │                    │  │    │  │                    │  │    │  │                │ │      │
│  │  │ Model A (LLM)      │  │    │  │ Model C (LLM)      │  │    │  │ Model X (LLM)  │ │      │
│  │  │ Model B (LLM)      │  │    │  │ Model D (LLM)      │  │    │  │ Model Y (LLM)  │ │      │
│  │  │                    │  │    │  │ Model E (Embed)    │  │    │  │ Model Z (...)  │ │      │
│  │  │ Bid: $0.0001/token │  │    │  │ Bid: $0.0002/token │  │    │  │ Bid: $0.0003.. │ │      │
│  │  │ Capacity: 10 slots │  │    │  │ Capacity: 5 slots  │  │    │  │ Capacity: 20.. │ │      │
│  │  └────────────────────┘  │    │  └────────────────────┘  │    │  └────────────────┘ │      │
│  │           │              │    │           │              │    │           │          │     │
│  │           ▼              │    │           ▼              │    │           ▼          │     │
│  │  ┌────────────────────┐  │    │  ┌────────────────────┐  │    │  ┌────────────────┐ │      │
│  │  │ LLM Infrastructure │  │    │  │ LLM Infrastructure │  │    │  │ LLM Infra...   │ │      │
│  │  │                    │  │    │  │                    │  │    │  │                │ │      │
│  │  │ • GPT-4            │  │    │  │ • Llama 3.1        │  │    │  │ • Claude 3.5   │ │      │
│  │  │ • GPT-3.5-turbo    │  │    │  │ • Mistral 8x7B     │  │    │  │ • Gemini Pro   │ │      │
│  │  │ • Custom models    │  │    │  │ • Venice Uncens..  │  │    │  │ • Custom ...   │ │      │
│  │  │                    │  │    │  │                    │  │    │  │                │ │      │
│  │  │ Hosted on:         │  │    │  │ Hosted on:         │  │    │  │ Hosted on:     │ │      │
│  │  │ • AWS Bedrock      │  │    │  │ • Local GPU Farm   │  │    │  │ • GCP Vertex   │ │      │
│  │  │ • OpenAI API       │  │    │  │ • llama.cpp        │  │    │  │ • Azure OpenAI │ │      │
│  │  └────────────────────┘  │    │  └────────────────────┘  │    │  └────────────────┘ │      │
│  │                          │    │                          │    │                      │     │
│  └──────────────────────────┘    └──────────────────────────┘    └──────────────────────┘     │
│                                                                                               │
└───────────────────────────────────────────────────────────────────────────────────────────────┘

                             ┌──────────────────────────────────────────────────────────────────┐
                             │  Arbitrum Blockchain                                             │
                             │                                                                  │
                             │  • MOR Token Contract                                            │
                             │  • MarketPlace Diamond                                           │
                             │  • Session Registry                                              │
                             │  • Payment Settlement                                            │
                             └──────────────────────────────────────────────────────────────────┘
```

## System Components

### 1. User Interfaces

#### OpenBeta Website (Documentation Site)
- **URL**: `api.mor.org` (production), `api.dev.mor.org` (development)
- **Technology**: Next.js (React framework)
- **Purpose**: Documentation hub, API guides, developer onboarding
- **Repository**: `Morpheus-Marketplace-API-Website`
- **Key Features**:
  - Interactive API documentation
  - Getting started guides
  - API key management interface
  - Analytics integration (Google Analytics 4 + GTM)
  - Cognito authentication for user account management

#### Active Models Site (Web Application)
- **URL**: `app.mor.org` (production), `app.dev.mor.org` (development)
- **Technology**: Next.js with real-time chat interface
- **Purpose**: Primary user-facing application for AI interactions
- **Repository**: `Morpheus-Marketplace-APP`
- **Key Features**:
  - Interactive chat interface with streaming responses
  - Model selection and discovery
  - Chat history management
  - Automation settings configuration
  - Real-time session status monitoring
  - API key creation and management
  - AWS Cognito authentication

#### Agent/Bot Applications
- **Integration Method**: Direct API calls to API Gateway
- **Authentication**: API keys (OpenAI-compatible)
- **Use Cases**: 
  - Automated workflows
  - Business integrations
  - Third-party applications
  - Custom AI agents

---

### 2. Authentication Layer (AWS Cognito)

#### AWS Cognito User Pool
- **Domain**: `auth.mor.org`
- **Region**: `us-east-2`
- **Protocol**: OAuth2 / OpenID Connect
- **Purpose**: Centralized user authentication and identity management

#### Authentication Flow
1. **User Registration/Login**:
   - User enters credentials via OpenBeta or Active Models Site
   - Cognito validates credentials and issues JWT tokens
   - Access token (short-lived) + Refresh token (long-lived)

2. **Token-Based Access**:
   - Frontend includes JWT in `Authorization: Bearer <token>` header
   - API Gateway validates JWT with Cognito
   - User identity retrieved for API key and settings management

3. **API Key Generation**:
   - Authenticated users create API keys via JWT-protected endpoints
   - API keys used for OpenAI-compatible endpoints
   - API keys map back to user accounts in database

#### Security Features
- Password hashing with bcrypt
- JWT token validation
- Secure token refresh flow
- OAuth2 standard compliance

---

### 3. API Gateway Layer (C-Node Interface)

#### Morpheus Marketplace API Gateway
- **URL**: `api.mor.org` (production), `api.dev.mor.org` (development)
- **Technology**: FastAPI (Python), PostgreSQL RDS, Docker/ECS
- **Repository**: `Morpheus-Marketplace-API`
- **Purpose**: Bridge between Web2 clients and Morpheus blockchain network

#### Core Responsibilities

##### 3.1 Authentication & User Management
**JWT Authentication (Cognito Integration)**:
- Validates Cognito JWT tokens
- Retrieves user profile information
- Manages user account settings

**API Key Management**:
- Generates unique API keys per user
- Secure key storage (hashed in database)
- Key lifecycle management (create, list, revoke)
- Last-used tracking and analytics

**Private Key Management**:
- Currently uses shared `FALLBACK_PRIVATE_KEY` environment variable
- All blockchain operations use this single fallback key
- Simplifies deployment and operations

##### 3.2 OpenAI-Compatible Endpoints
**Chat Completions** (`POST /api/v1/chat/completions`):
- Accepts OpenAI-format requests
- Maps model names to blockchain IDs
- Manages or creates sessions automatically
- Streams responses back to clients
- Handles both streaming and non-streaming modes

**Models** (`GET /api/v1/models`):
- Lists all available models from providers
- Syncs with `active.mor.org/active_models.json`
- Provides model metadata (pricing, capabilities)
- Automatic background synchronization (every 24 hours)

**Embeddings** (`POST /api/v1/embeddings`):
- Text embedding generation
- OpenAI-compatible format
- Routes to appropriate embedding models

##### 3.3 Session Management & Automation
**Automated Session Creation**:
- Checks user automation settings on every chat request
- Creates blockchain sessions automatically if enabled
- Manages session lifecycle (creation, renewal, expiration)
- Cleanup expired sessions (background task every 15 minutes)

**Session Lifecycle**:
- `POST /api/v1/session/bidsession` - Create bid-based session
- `POST /api/v1/session/modelsession` - Create model-specific session
- `POST /api/v1/session/pingsession` - Check session health
- `POST /api/v1/session/closesession` - Terminate session
- `POST /api/v1/session/approve` - Approve token spending

**Automation Settings**:
- Per-user automation preferences
- Configurable session duration
- Enable/disable automatic session management

##### 3.4 Model Routing & Discovery
**Model Mapping**:
- Translates OpenAI model names (e.g., `gpt-4`) to blockchain model IDs (64-char hex)
- Maintains local `models.json` cache
- Automatic fallback to default models if requested model unavailable

**Active Model Synchronization**:
- Fetches latest models from `https://active.mor.org/active_models.json`
- Background sync every 24 hours (configurable)
- Startup sync on application launch
- Preserves local-only model configurations

**Model Discovery**:
- Real-time model availability from blockchain
- Provider bid information
- Model capabilities and pricing

##### 3.5 Database Layer (PostgreSQL RDS)

**Production Tables (4 Core Tables)**:

1. **`users`** - User accounts and authentication
   - `id`, `name`, `email`, `hashed_password`, `is_active`
   - Created via Cognito authentication
   - Foundation for all user operations

2. **`api_keys`** - API key management (HIGH TRAFFIC)
   - `id`, `key_prefix`, `hashed_key`, `user_id`, `name`, `last_used_at`, `is_active`
   - Read on every authenticated request
   - Tracks API key usage patterns

3. **`sessions`** - Active session tracking (HIGHEST TRAFFIC)
   - `id` (blockchain session ID), `user_id`, `api_key_id`, `model`, `type`, `expires_at`, `is_active`
   - Frequent reads/writes during chat operations
   - Background cleanup of expired sessions
   - Enforces one active session per API key

4. **`user_automation_settings`** - Session automation configuration
   - `user_id`, `is_enabled`, `session_duration`
   - Checked on every `/chat/completions` request
   - Controls automatic session creation behavior

**Database Access Patterns**:
- Every API request → `api_keys` lookup (authentication)
- Every chat completion → `sessions` + `user_automation_settings` check
- Background cleanup → `sessions` expiration query every 15 minutes
- User management → `users` CRUD operations

#### Infrastructure & Deployment

**Docker/ECS Deployment**:
- Multi-stage Docker builds with Poetry dependency management
- AWS ECS with auto-scaling
- Semantic versioning via CI/CD pipeline
- Database migrations via Alembic (automatic on deployment)

**CI/CD Pipeline** (`main`, `test`, `dev` branches):
- Automated testing with PostgreSQL test database
- Docker image build and push to GitHub Container Registry (GHCR)
- ECS deployment with health checks
- Automatic rollback on deployment failure
- Git tagging for release tracking

**Environments**:
- Production: `api.mor.org` (from `main` branch)
- Development: `api.dev.mor.org` (from `test` branch)
- Local Testing: Docker Compose with hot reload

**Logging & Monitoring**:
- Structured JSON logging in production
- Component-level log level control
- CloudWatch integration
- Health check endpoints with version validation

---

### 4. Consumer Node (C-Node) - Proxy Router

#### Morpheus-Lumerin-Node (proxy-router)
- **Purpose**: Blockchain-connected routing layer between API Gateway and Provider Nodes
- **Repository**: `Morpheus-Lumerin-Node` (specifically `proxy-router` component)
- **Technology**: Go (Golang), blockchain client libraries

#### Core Responsibilities

##### 4.1 Blockchain Integration
**Arbitrum Blockchain Connectivity**:
- **MainNet**: Chain ID `42161` (production)
- **TestNet**: Sepolia Arbitrum Chain ID `421614` (development)

**Smart Contract Interaction**:
- **MOR Token Contract**: `0x092bAaDB7DEf4C3981454dD9c0A0D7FF07bCFc86` (MainNet)
- **Diamond MarketPlace Contract**: `0xDE819AaEE474626E3f34Ef0263373357e5a6C71b` (MainNet)
- Monitors contract events (session creation, provider bids, payments)
- Handles MOR token transactions (staking, session payments)
- Manages ETH gas fees for blockchain operations

**Session Registration**:
- Registers sessions on-chain with session ID, consumer, provider, model
- Validates session parameters and authorization
- Tracks session state on blockchain

##### 4.2 Session & Request Routing
**Secure Session Management**:
- Establishes secure communication channels between consumers and providers
- Validates session authorization before routing requests
- Tracks session state (active, idle, expired)
- Enforces session capacity policies

**Request/Response Routing**:
- Routes prompts from consumers to selected provider models
- Streams inference responses back to consumers
- Handles concurrent requests within same session
- Manages chat context (optional `PROXY_STORE_CHAT_CONTEXT` setting)

**Capacity Management Policies**:
- **Simple**: Fixed slot allocation per session
- **Idle Timeout**: Releases slots after 15 minutes of inactivity

**Chat Context Management** (Configurable):
- **Enabled** (`PROXY_STORE_CHAT_CONTEXT=true`):
  - Stores chat histories locally
  - API endpoints for chat CRUD operations (`/v1/chats`)
  - Injects context automatically via `chat_id` header
- **Disabled** (`PROXY_STORE_CHAT_CONTEXT=false`):
  - Client manages full conversation history
  - Stateless request forwarding

##### 4.3 Provider Discovery & Bidding
**Active Provider Discovery**:
- Listens for provider registration events on blockchain
- Maintains registry of available models and providers
- Monitors provider reputation and uptime

**Bid Evaluation**:
- Compares provider bids for requested models
- Considers price, capacity, and reliability
- Selects optimal provider for consumer request
- Enables price competition among providers

**Session Creation Flow**:
1. Consumer requests model via API Gateway
2. C-Node queries blockchain for available providers/bids
3. Evaluates bids based on pricing and availability
4. Consumer approves selected bid and stakes MOR tokens
5. C-Node creates session on blockchain
6. Routes prompts to provider model

---

### 5. Provider Nodes (P-Nodes)

#### Multiple Independent Providers
- **Purpose**: Host and serve AI models on the Morpheus network
- **Repository**: `Morpheus-Lumerin-Node` (provider mode)
- **Deployment**: Distributed across various infrastructure providers

#### Provider Architecture

##### 5.1 Blockchain Wallet & Economics
**MOR Token Staking**:
- Providers stake MOR tokens to offer models
- Stake amount impacts visibility and trust
- Potential for slash conditions (service quality)

**Bid Management**:
- Providers submit bids for their models (price per token)
- Dynamic pricing based on demand and capacity
- Competitive marketplace for AI services

**Payment Receipt**:
- Receives MOR token payments for inference services
- Session-based payment settlement
- Automatic fund distribution from smart contract

##### 5.2 Model Registry
**Per-Provider Model Catalog**:
- Each provider registers multiple models on blockchain
- Model metadata:
  - Model name (e.g., `gpt-4`, `llama-3.1-8b`)
  - Blockchain model ID (64-character hex identifier)
  - Type (LLM, Embedding, etc.)
  - Capabilities (context length, features)

**Pricing & Capacity**:
- Price per token (competitive bidding)
- Concurrent session capacity (e.g., 10 slots)
- Model-specific pricing tiers
- Real-time availability updates

##### 5.3 LLM Infrastructure
**Model Hosting Options**:
- **Cloud Providers**: AWS Bedrock, GCP Vertex AI, Azure OpenAI
- **Local Infrastructure**: GPU farms, custom hardware
- **Hosted APIs**: OpenAI API, Anthropic, Cohere
- **Self-Hosted**: llama.cpp, vLLM, TensorRT

**Supported Model Types**:
- Large Language Models (LLMs): GPT-4, Claude, Llama, Mistral, Venice Uncensored, etc.
- Embedding Models: text-embedding-ada-002, BGE-M3, etc.
- Fine-tuned Custom Models: Domain-specific trained models
- Open-Source Models: Community-contributed models

**Provider Examples**:
- **Provider #1**: OpenAI models via API (GPT-4, GPT-3.5-turbo)
- **Provider #2**: Local Llama 3.1 and Mistral on GPU farm
- **Provider #3**: Claude 3.5 via Anthropic API
- **Provider #N**: Custom fine-tuned models for specific domains

**Infrastructure Diversity**:
- Decentralized hosting across multiple geographic regions
- Redundancy and fault tolerance
- Mix of commercial APIs and self-hosted solutions
- Competitive pricing through market dynamics

---

### 6. Blockchain Layer (Arbitrum)

#### Smart Contracts
**MOR Token Contract**:
- ERC-20 token for network payments
- Used for staking (providers) and payments (consumers)
- Governed by smart contract economics

**Diamond MarketPlace Contract**:
- Central registry for providers and models
- Bid management and session creation
- Payment settlement and distribution
- Event emission for C-Node monitoring

**Session Registry**:
- On-chain record of all active sessions
- Session metadata (consumer, provider, model, expiration)
- Immutable audit trail

**Payment Settlement**:
- Automated payment distribution based on usage
- Escrow mechanism for session payments
- Dispute resolution (if implemented)

---

## Data Flow: End-to-End Request

### Example: Human User Sends Chat Message

```
1. USER INTERACTION
   User types message in app.mor.org chat interface
   ↓

2. FRONTEND (Active Models Site)
   - Retrieves user's JWT token (from Cognito login)
   - Retrieves user's API key (from account settings)
   - Constructs OpenAI-format request:
     {
       "model": "gpt-4",
       "messages": [{"role": "user", "content": "Hello"}],
       "stream": true
     }
   ↓

3. API GATEWAY (Morpheus-Marketplace-API)
   a. Authentication:
      - Validates API key in Authorization header
      - Looks up api_keys table → finds user_id
      - Retrieves user from users table
   
   b. Model Routing:
      - Maps "gpt-4" to blockchain model ID (64-char hex)
      - Queries models.json for model metadata
      - Falls back to default model if not found
   
   c. Session Management:
      - Queries sessions table for active session
      - If no active session:
        * Checks user_automation_settings table
        * If automation enabled: creates new session
        * Calls C-Node to create blockchain session
   
   d. Request Forwarding:
      - Forwards request to C-Node proxy-router
      - Includes session_id, model_id, user_id
      - Sets up streaming response handler
   ↓

4. C-NODE (Proxy-Router)
   a. Session Validation:
      - Verifies session exists on blockchain
      - Checks session hasn't expired
      - Validates consumer authorization
   
   b. Provider Selection:
      - Session already has assigned provider (from session creation)
      - Validates provider is still active
      - Establishes connection to provider
   
   c. Request Routing:
      - Routes prompt to Provider Node
      - Maintains session context (if enabled)
      - Sets up bidirectional streaming
   ↓

5. P-NODE (Provider)
   a. Request Processing:
      - Receives prompt from C-Node
      - Validates session authorization
      - Prepares model for inference
   
   b. Model Inference:
      - Sends prompt to LLM infrastructure
      - LLM generates tokens (e.g., GPT-4 API call)
      - Streams tokens back as they're generated
   
   c. Response Streaming:
      - Streams tokens to C-Node
      - Tracks token count for billing
      - Manages model capacity
   ↓

6. C-NODE (Response)
   - Receives streaming tokens from Provider
   - Forwards tokens to API Gateway
   - Updates session usage metrics
   - Tracks token count for blockchain settlement
   ↓

7. API GATEWAY (Response)
   - Receives streaming tokens from C-Node
   - Formats response in OpenAI SSE format
   - Streams response to frontend
   - Updates sessions table (last activity)
   - Updates api_keys table (last_used_at)
   ↓

8. FRONTEND (Active Models Site)
   - Receives Server-Sent Events (SSE)
   - Renders tokens in chat interface
   - Displays streaming response in real-time
   - Saves message to chat history
   ↓

9. USER INTERFACE
   User sees complete AI response
```

### Example: Bot/Agent Direct API Call

```
1. AGENT APPLICATION
   - Maintains API key (generated via /api/v1/auth/keys)
   - Constructs OpenAI-compatible request
   - No Cognito JWT needed (uses API key only)
   ↓

2. API GATEWAY
   [Same flow as steps 3-7 above]
   ↓

3. AGENT APPLICATION
   - Receives JSON response
   - Processes AI response
   - Continues automated workflow
```

---

## Repository Links & Management

### Core Repositories

#### 1. Morpheus-Marketplace-API
- **GitHub**: `https://github.com/MorpheusAIs/Morpheus-Marketplace-API`
- **Purpose**: API Gateway (FastAPI + PostgreSQL)
- **Deployment**: AWS ECS
- **Branch Strategy**:
  - `main` → Production (`api.mor.org`)
  - `test` → Development (`api.dev.mor.org`)
  - `dev` → Testing only
- **Key Technologies**: Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL, Docker, AWS KMS
- **CI/CD**: GitHub Actions → GHCR → AWS ECS

#### 2. Morpheus-Marketplace-API-Website
- **GitHub**: `https://github.com/MorpheusAIs/Morpheus-Marketplace-API-Website`
- **Purpose**: Documentation website (OpenBeta)
- **Deployment**: AWS Amplify
- **Branch Strategy**:
  - `main` → Production (`api.mor.org`)
  - `dev` → Development (`api.dev.mor.org`)
- **Key Technologies**: Next.js, React, TypeScript, Tailwind CSS
- **CI/CD**: AWS Amplify auto-deploy

#### 3. Morpheus-Marketplace-APP
- **GitHub**: `https://github.com/MorpheusAIs/Morpheus-Marketplace-APP`
- **Purpose**: Web application interface (Active Models Site)
- **Deployment**: AWS Amplify
- **Branch Strategy**:
  - `main` → Production (`app.mor.org`)
  - `dev` → Development (`app.dev.mor.org`)
- **Key Technologies**: Next.js, React, TypeScript, AWS Cognito, Tailwind CSS
- **CI/CD**: AWS Amplify auto-deploy

#### 4. Morpheus-Lumerin-Node
- **GitHub**: `https://github.com/MorpheusAIs/Morpheus-Lumerin-Node`
- **Purpose**: C-Node (proxy-router) and P-Node software
- **Components**:
  - `proxy-router/` - Consumer Node implementation
  - `provider/` - Provider Node setup
  - `ui-desktop/` - Desktop chat application
  - `cli/` - Command-line interface
- **Branch Strategy**:
  - `main` → MainNet deployment (Arbitrum Chain ID 42161)
  - `test` → TestNet deployment (Sepolia Arbitrum Chain ID 421614)
- **Key Technologies**: Go (Golang), Blockchain clients, IPFS
- **Deployment**: Docker, packaged releases

#### 5. Morpheus-Infra
- **GitHub**: `https://github.com/MorpheusAIs/Morpheus-Infra`
- **Purpose**: Infrastructure as Code (Terraform) for AWS resources
- **Components**:
  - VPC and networking configuration
  - RDS database setup
  - ECS cluster management
  - CloudWatch monitoring
  - Security groups and IAM roles
- **Key Technologies**: Terraform, AWS, HCL
- **Management**: Terragrunt for environment management

### External Dependencies

#### Active Models Registry
- **URL**: `https://active.mor.org/active_models.json`
- **Purpose**: Canonical source of active models on the network
- **Used By**: API Gateway (automatic synchronization)
- **Update Frequency**: Real-time (API syncs every 24 hours)

#### Arbitrum Blockchain
- **MainNet**: Arbitrum One (Chain ID: 42161)
  - Explorer: `https://arbiscan.io/`
  - MOR Token: `0x092bAaDB7DEf4C3981454dD9c0A0D7FF07bCFc86`
  - MarketPlace: `0xDE819AaEE474626E3f34Ef0263373357e5a6C71b`
- **TestNet**: Sepolia Arbitrum (Chain ID: 421614)
  - Explorer: `https://sepolia.arbiscan.io/`
  - MOR Token: `0x34a285a1b1c166420df5b6630132542923b5b27e`
  - MarketPlace: `0xb8C55cD613af947E73E262F0d3C54b7211Af16CF`

#### AWS Services
- **Cognito**: User authentication (`auth.mor.org`)
- **RDS**: PostgreSQL database (production + development instances)
- **ECS**: Container orchestration for API Gateway
- **Amplify**: Frontend hosting (websites and app)
- **KMS**: Private key encryption master key
- **CloudWatch**: Logging and monitoring
- **Secrets Manager**: Database credentials

---

## Key Integration Points

### 1. API Gateway ↔ C-Node Communication
- **Protocol**: HTTP/HTTPS via httpx async client
- **Authentication**: Username/password (configured in proxy-router)
- **Endpoints Called**:
  - `/blockchain/approve` - Token spending approval
  - `/session/create` - Blockchain session creation
  - `/chat/completions` - Prompt forwarding (proxy-style)
- **Data Passed**:
  - Session ID
  - Model blockchain ID
  - Shared fallback private key (via FALLBACK_PRIVATE_KEY environment variable)
  - Request payload (OpenAI format)

### 2. C-Node ↔ Blockchain Communication
- **Protocol**: JSON-RPC over HTTP/HTTPS
- **Web3 Library**: Ethereum client libraries (Go)
- **Operations**:
  - Read blockchain events (provider bids, session updates)
  - Write transactions (session creation, payment settlement)
  - Query contract state (model availability, pricing)
- **Gas Management**: ETH required for transaction fees

### 3. C-Node ↔ P-Node Communication
- **Protocol**: HTTP/HTTPS with custom session headers
- **Security**: Session-based authentication and authorization
- **Data Format**: OpenAI-compatible JSON format
- **Streaming**: Server-Sent Events (SSE) for streaming responses
- **Session Context**: Optional chat history injection (if PROXY_STORE_CHAT_CONTEXT enabled)

### 4. Frontend ↔ API Gateway Communication
**Authentication**:
- Cognito JWT for user management endpoints
- API keys for AI inference endpoints

**Endpoints Used by Frontends**:
- `/api/v1/auth/me` - User profile (JWT)
- `/api/v1/auth/keys` - API key management (JWT)
- `/api/v1/automation/settings` - Automation configuration (JWT)
- `/api/v1/models` - List models (public)
- `/api/v1/chat/completions` - Chat inference (API key)
- `/api/v1/chat-history/*` - Chat history CRUD (API key)

---

## Security Architecture

### Authentication Hierarchy
1. **Cognito JWT** (user identity):
   - User registration, login, profile management
   - API key generation and management
   - Automation settings configuration

2. **API Keys** (service access):
   - AI inference operations (chat completions, embeddings)
   - Chat history management
   - Session operations
   - Model queries

### Data Encryption
- **In Transit**: TLS/HTTPS for all communications
- **At Rest**:
  - Database: RDS encryption at rest
  - Private Keys: AES encryption with AWS KMS master key
  - API Keys: SHA-256 hashed in database (store only hash + prefix)
  - Passwords: Bcrypt hashed user passwords

### Blockchain Security
- **Private Key Management**:
  - Shared `FALLBACK_PRIVATE_KEY` environment variable for all blockchain operations
  - Simplifies deployment while maintaining blockchain functionality
- **Session Authorization**: On-chain validation of session permissions
- **Payment Escrow**: Smart contract-managed payment holds

---

## Scalability & Performance

### Database Optimization
**High-Traffic Tables**:
- `sessions` - Indexed on `api_key_id`, `is_active`, `expires_at`
- `api_keys` - Indexed on `key_prefix` (for fast authentication)
- `users` - Indexed on `email` (for login)

**Background Tasks**:
- Session cleanup every 15 minutes (marks expired sessions as inactive)
- Model synchronization every 24 hours (fetches from active.mor.org)

**Query Optimization**:
- Unique constraint on active sessions per API key
- Efficient expiration queries with indexed timestamps
- Fast authentication via hashed key prefix lookup

### API Gateway Scaling
- **ECS Auto-scaling**: Based on CPU/memory metrics
- **Health Checks**: 5-minute stabilization period
- **Circuit Breaker**: Automatic rollback on deployment failure
- **Load Balancing**: ECS service discovery and ALB integration

### C-Node Scaling
- Horizontal scaling of proxy-router instances
- Session affinity for sticky routing
- Provider pool management for load distribution

---

## Monitoring & Observability

### Logging
- **API Gateway**: Structured JSON logging (LOG_JSON=true)
- **Component-Level Control**: Separate log levels for AUTH, API, MODELS, PROXY, DATABASE
- **CloudWatch Integration**: Centralized log aggregation

### Metrics
- **Health Endpoints**:
  - `/health` - API and database health
  - `/health/models` - Model service health
- **Session Metrics**: Active sessions, session duration, expiration tracking
- **API Key Metrics**: Last used timestamp, usage patterns

### Analytics
- **Google Analytics 4**: User behavior tracking
- **Google Tag Manager**: Event-driven analytics
- **Custom Events**: API key creation, session creation, chat interactions


## Glossary

- **C-Node**: Consumer Node (proxy-router) - intermediary between API Gateway and Provider Nodes
- **P-Node**: Provider Node - hosts and serves AI models on the network
- **MOR Token**: Morpheus utility token for network payments and staking
- **Session**: Blockchain-registered interaction between consumer and provider
- **Bid**: Provider's offer to serve a model at a specific price
- **API Key**: User-generated key for accessing OpenAI-compatible endpoints
- **JWT**: JSON Web Token for Cognito user authentication
- **Blockchain ID**: 64-character hexadecimal identifier for models on blockchain
- **Fallback Model**: Default model used when requested model is unavailable
- **Automation Settings**: User preference for automatic session creation

---

## Document Version

- **Version**: 1.1
- **Last Updated**: October 30, 2025
- **Maintainer**: Morpheus Development Team
- **Focus**: Current production state and operational architecture
- **Status**: Living Document (updated as architecture evolves)

---

**This document is a comprehensive architectural overview intended for new contributors. It describes the current production state of the Morpheus Marketplace ecosystem and should be treated as a standalone reference.**

