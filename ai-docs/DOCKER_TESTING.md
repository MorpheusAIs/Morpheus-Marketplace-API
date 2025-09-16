# Docker Container Testing

Test your API changes using containerized environments that match production!

## 🚀 Quick Start - Self-Contained Local Testing (Recommended)

**No external dependencies needed!** This approach bypasses authentication and uses a local database:

```bash
# One-time setup
cp env.local.example .env.local

# Start testing environment
./scripts/test_local.sh
```

**What this gives you:**
- ✅ **No Authentication Required** - Both JWT and API key auth bypassed
- ✅ **Fresh Database** - PostgreSQL with auto-migrations on every start
- ✅ **Hot Reload** - Code changes apply instantly
- ✅ **All Endpoints Work** - Test everything without tokens/keys
- ✅ **Ephemeral** - Clean state every time

**Access:**
- **API**: http://localhost:8000
- **Swagger UI**: http://localhost:8000/docs (all endpoints work without auth!)
- **Health**: http://localhost:8000/health

## 🔧 Authentication Bypass Details

### How It Works
When `LOCAL_TESTING_MODE=true` in your `.env.local`:

**JWT Endpoints (auth/*):**
- `/api/v1/auth/me` ✅ Works without Bearer token
- `/api/v1/auth/keys` ✅ Works without Bearer token  
- All other JWT endpoints ✅ Bypassed

**API Key Endpoints:**
- `/api/v1/chat-history/*` ✅ Works without API key
- `/api/v1/models` ✅ Works without API key
- All other API key endpoints ✅ Bypassed

**Test User:**
- Email: `test@local.dev`
- Automatically created on first request
- Consistent across all endpoints

### Database Auto-Migration
Every startup automatically:
1. **Waits** for PostgreSQL to be ready
2. **Runs** `alembic upgrade head` (all migrations)
3. **Verifies** all required tables exist
4. **Starts** API with fresh, current schema

## 🧪 Legacy Testing Options

### Option 1: Simple Container Test (Production Config)
```bash
./scripts/docker-test.sh  # If exists
```
- Uses production authentication (Cognito required)
- Requires external database connection
- Good for production config testing

### Option 2: Quick Rebuild (For Changes)
```bash
./scripts/docker-rebuild.sh  # If exists
```
- Rebuilds and restarts container
- Perfect for testing code changes
- Faster than full docker-compose

## 🧪 Testing All Endpoints (Local Mode)

1. **Start Environment**: `./scripts/test_local.sh`
2. **Open Swagger UI**: http://localhost:8000/docs
3. **Test Any Endpoint**:
   - No "Authorize" button needed - auth bypassed!
   - Click "Try it out" on any endpoint
   - Execute directly without tokens
4. **Test Specific Features**:
   - **Chat History**: Create/list chats and messages
   - **API Keys**: Create/manage API keys
   - **Models**: List available models
   - **User Info**: Get current user details

## 🔄 Development Workflow (Local Mode)

1. **Start Environment**: `./scripts/test_local.sh`
2. **Make code changes** (hot reload active)
3. **Test immediately**: http://localhost:8000/docs
4. **Changes apply instantly** - no rebuild needed!
5. **Stop/restart** for database changes: Ctrl+C, then restart

## 🧪 Testing OAuth2 (Production Mode)

For testing actual OAuth2/Cognito integration:

1. **Use production config** with real Cognito settings
2. **Start Container**: Use legacy docker scripts
3. **Open Swagger UI**: http://localhost:8000/docs
4. **Test OAuth2 Modal**:
   - Click green "Authorize" button
   - Verify `client_id` shows the configured client ID
   - Check browser console for debug messages

## 🐳 Docker Commands

### Manual Commands
```bash
# Build image
docker build -t morpheus-api-test .

# Run with .env file (same as production)
docker run -d --name morpheus-api-test -p 8000:8000 \
  --env-file .env \
  morpheus-api-test

# Check logs
docker logs morpheus-api-test

# Stop and remove
docker stop morpheus-api-test && docker rm morpheus-api-test
```

### Docker Compose Commands
```bash
# Start full environment
docker-compose up --build -d

# View logs
docker-compose logs api

# Stop everything
docker-compose down
```

## 🎯 What to Test (Local Mode)

- **✅ All API Endpoints**: Every endpoint works without authentication
- **✅ Chat Functionality**: Create chats, add messages, list history
- **✅ Model Service**: List models, test routing
- **✅ Database Operations**: All CRUD operations work
- **✅ Auto-Migration**: Database schema always current
- **✅ Hot Reload**: Code changes apply instantly

## 🎯 What to Test (Production Mode)

- **✅ Client ID**: Should show the configured client ID in modal
- **✅ OAuth2 Flow**: Modal opens and stays open
- **✅ Console Logs**: Debug messages show correct client_id
- **✅ Cognito Redirect**: Should redirect to `https://auth.mor.org`

## 🚨 Benefits of Local Container Testing

1. **Zero Configuration**: No external dependencies needed
2. **Authentication Bypass**: Test all endpoints immediately
3. **Auto-Migration**: Database always up-to-date
4. **Hot Reload**: Instant code changes
5. **Ephemeral**: Clean state every restart
6. **Fast Startup**: Ready in ~10 seconds
7. **No Deployment Wait**: Test locally instantly

## ⚙️ Environment Setup

### Local Testing Setup (.env.local)
```bash
# Copy template and customize if needed
cp env.local.example .env.local

# Default settings work out of the box:
LOCAL_TESTING_MODE=true
BYPASS_COGNITO_AUTH=true
ACTIVE_MODELS_URL=https://active.dev.mor.org/active_models.json
PROXY_ROUTER_URL=http://router.dev.mor.org:8082
ENVIRONMENT=local
```

### Production Testing Setup (.env)
```bash
# Check if OAuth2 settings are present
grep -E "COGNITO_|API_V1_STR" .env
```

**Required OAuth2 Settings:**
```bash
COGNITO_USER_POOL_ID=your-user-pool-id
COGNITO_CLIENT_ID=your-cognito-client-id
COGNITO_REGION=us-east-2
COGNITO_DOMAIN=your-cognito-domain
API_V1_STR=/api/v1
LOCAL_TESTING_MODE=false  # Important!
```

### If Missing OAuth2 Settings
```bash
# Add OAuth2 settings to your .env file
cat >> .env << EOF
COGNITO_USER_POOL_ID=your-user-pool-id
COGNITO_CLIENT_ID=your-cognito-client-id
COGNITO_REGION=us-east-2
COGNITO_DOMAIN=your-cognito-domain
API_V1_STR=/api/v1
LOCAL_TESTING_MODE=false
EOF
```

## 📁 Key Files

**Local Testing:**
- `scripts/test_local.sh` - Self-contained local testing
- `docker-compose.local.yml` - Local container configuration
- `env.local.example` - Local testing template
- `scripts/start_local_dev.sh` - Container startup with auto-migration

**Legacy Testing:**
- `docker-test.sh` - Simple container test (if exists)
- `docker-rebuild.sh` - Quick rebuild for changes (if exists)
- `DOCKER_TESTING.md` - This guide

## 🧹 Cleanup

### Local Testing Cleanup
```bash
# Stop local testing environment
docker compose -f docker-compose.local.yml down

# Remove volumes (optional - for complete cleanup)
docker compose -f docker-compose.local.yml down --volumes
```

### Legacy Testing Cleanup
```bash
# Remove test containers
docker stop morpheus-api-test && docker rm morpheus-api-test
docker-compose down

# Remove test image
docker rmi morpheus-api-test
```

## 🎉 Summary

**For Development**: Use `./scripts/test_local.sh` - no auth needed, instant testing!

**For Production Config**: Use legacy docker scripts with real Cognito settings.

Now you can test **all API endpoints instantly** without authentication or deployment waits! 🎊
