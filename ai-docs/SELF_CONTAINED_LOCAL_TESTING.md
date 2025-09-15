# Self-Contained Local Testing

This guide provides a **completely self-contained** local testing setup that requires **no external dependencies** - perfect for development and sharing with other developers.

## Overview

### 🎯 What This Provides

✅ **Zero External Dependencies** - No need for dev database access  
✅ **No Cognito Setup** - Authentication bypassed with test user  
✅ **Local PostgreSQL** - Ephemeral database that starts fresh each time  
✅ **Hot Reload Development** - Code changes apply immediately  
✅ **Full API Functionality** - All endpoints work including chat history  
✅ **Easy Sharing** - Other developers can run immediately  

### 🔄 Two Testing Approaches

| Feature | External Services | Self-Contained |
|---------|-------------------|-----------------|
| **Setup Time** | Requires dev credentials | Immediate |
| **Dependencies** | Dev DB + Cognito | None |
| **Authentication** | Real Cognito JWT | Bypassed |
| **Database** | External dev DB | Local ephemeral |
| **Sharing** | Needs credentials | Works anywhere |
| **Use Case** | Integration testing | Development |

## Self-Contained Setup (Recommended for Development)

### 1. Quick Start

```bash
# Copy the example (defaults work fine)
cp env.local.example .env.local

# Start everything
./scripts/test_local.sh
```

That's it! No credentials needed.

### 2. What You Get

**🚀 API Available At:**
- **Base URL:** http://localhost:8000
- **Health Check:** http://localhost:8000/health
- **API Docs:** http://localhost:8000/docs

**👤 Test User:**
- **Email:** test@local.dev
- **Cognito ID:** local-test-user
- **Authentication:** Automatically bypassed

**🗄️ Database:**
- **Type:** Local PostgreSQL
- **Data:** Fresh on each start (ephemeral)
- **Port:** 5433 (to avoid conflicts)
- **Migrations:** Auto-run on startup
- **Verification:** Tables verified before API starts

### 3. Testing Workflow

```bash
# Start environment
./scripts/test_local.sh

# In another terminal - test the API
curl http://localhost:8000/health

# Test chat history (no API key needed in local mode)
curl -X POST http://localhost:8000/api/v1/chat-history/chats \
  -H "Content-Type: application/json" \
  -d '{"title": "Local Test Chat"}'

# Create API keys for testing
curl -X POST http://localhost:8000/api/v1/auth/api-keys \
  -H "Content-Type: application/json" \
  -d '{"name": "Local Test Key"}'
```

### 4. Development Features

**🔥 Hot Reload:**
- Edit code in `src/`
- Changes apply immediately
- No restart needed

**📊 Full Logging:**
- All API operations logged
- Database queries visible
- Authentication bypass logged

**🧪 Test Data:**
- Fresh database each start
- Consistent test user
- Predictable state

## Configuration

### Environment Variables (.env.local)

```bash
# Local Testing Mode (bypasses Cognito)
BYPASS_COGNITO_AUTH=true
LOCAL_TESTING_MODE=true

# Model Service (external - works fine)
ACTIVE_MODELS_URL=https://active.dev.mor.org/active_models.json
DEFAULT_FALLBACK_MODEL=mistral-31-24b

# Proxy Router (can use dev or mock)
PROXY_ROUTER_URL=http://router.dev.mor.org:8082

# Development Settings
LOG_LEVEL=DEBUG
ENVIRONMENT=local
```

### Database Configuration

**Automatic Setup:**
- PostgreSQL 15 Alpine
- Database: `morpheus_local_db`
- User: `morpheus_local`
- Password: `local_dev_password`
- Port: `5433` (external)
- **Auto-Migration:** All Alembic migrations run on startup
- **Table Verification:** Ensures all required tables exist

**Data Persistence:**
- ✅ **During session** - Data persists while containers run
- ❌ **Between sessions** - Fresh start each time (by design)

**Startup Process:**
1. 🗄️ **Database starts** - PostgreSQL container initializes
2. ⏳ **Wait for DB** - API container waits for database readiness
3. 🔄 **Run migrations** - `alembic upgrade head` creates all tables
4. ✅ **Verify structure** - Confirms all required tables exist
5. 🚀 **Start API** - Uvicorn starts with hot reload enabled

## Authentication Bypass

### How It Works

```python
# In local testing mode, this bypasses Cognito:
async def get_current_user(db, token):
    if is_local_testing_mode():
        return await get_or_create_test_user(db)
    # ... normal Cognito validation
```

### Test User Details

```json
{
  "cognito_user_id": "local-test-user",
  "email": "test@local.dev", 
  "name": "Local Test User",
  "id": 1
}
```

### Security Notes

⚠️ **Local Testing Only:**
- `BYPASS_COGNITO_AUTH=true` **ONLY** works with `LOCAL_TESTING_MODE=true`
- Authentication bypass **NEVER** active in production
- Startup logs clearly indicate when bypass is active

## Testing Scenarios

### 1. API Development
```bash
# Start environment
./scripts/test_local.sh

# Edit src/api/v1/your_endpoint.py
# Changes auto-reload

# Test immediately
curl http://localhost:8000/api/v1/your-endpoint
```

### 2. Chat History Testing
```bash
# Create chat
curl -X POST http://localhost:8000/api/v1/chat-history/chats \
  -H "Content-Type: application/json" \
  -d '{"title": "Test Chat"}'

# Add message  
curl -X POST http://localhost:8000/api/v1/chat-history/chats/{chat_id}/messages \
  -H "Content-Type: application/json" \
  -d '{"role": "user", "content": "Hello"}'

# List chats
curl http://localhost:8000/api/v1/chat-history/chats
```

### 3. API Key Testing
```bash
# Create API key
curl -X POST http://localhost:8000/api/v1/auth/api-keys \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Key"}'

# Use API key for authenticated requests
curl -H "Authorization: Bearer sk-xxxxxx" \
  http://localhost:8000/api/v1/models
```

## Troubleshooting

### Container Won't Start
```bash
# Check logs
docker-compose -f docker-compose.local.yml logs

# Check ports
lsof -i :8000
lsof -i :5433
```

### Database Issues
```bash
# Connect to database directly
docker exec -it $(docker ps -q -f name=db-local) \
  psql -U morpheus_local -d morpheus_local_db

# Check tables
\dt
```

### Authentication Issues
```bash
# Verify bypass is active (should see in logs)
docker-compose -f docker-compose.local.yml logs api-local | grep "LOCAL TESTING"
```

## Comparison with External Services Approach

### When to Use Self-Contained
✅ **Feature development** - Building new endpoints  
✅ **Bug fixing** - Debugging issues  
✅ **Sharing with developers** - No credential setup needed  
✅ **CI/CD testing** - Consistent environment  
✅ **Offline development** - No internet required (except for models)  

### When to Use External Services  
✅ **Integration testing** - Real environment behavior  
✅ **Authentication testing** - Real Cognito flows  
✅ **Performance testing** - Real database performance  
✅ **Final validation** - Before production deployment  

## Best Practices

### 1. Development Workflow
```bash
# Daily development
./scripts/test_local.sh  # Self-contained


# Production deployment
git push                      # CI/CD pipeline
```

### 2. Code Changes
- ✅ **Edit freely** - Hot reload handles changes
- ✅ **Test immediately** - No deployment delays
- ✅ **Fresh state** - Restart for clean slate

### 3. Sharing with Team
```bash
# Share these files:
# - docker-compose.local.yml
# - env.local.example  
# - scripts/test_local_full.sh

# Team members just run:
cp env.local.example .env.local
./scripts/test_local.sh
```

## Advanced Usage

### Custom Model Service
```bash
# In .env.local, use local mock:
ACTIVE_MODELS_URL=http://localhost:3000/mock-models.json
```

### Database Persistence
```bash
# To keep data between sessions, comment out --volumes in script:
# docker-compose -f docker-compose.local.yml down --remove-orphans
```

### Multiple Environments
```bash
# Run on different port
docker-compose -f docker-compose.local.yml up -p 8001:8000
```

This self-contained approach provides the **perfect development environment** - fast, reliable, and completely independent! 🚀
