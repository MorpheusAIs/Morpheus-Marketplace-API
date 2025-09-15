# Docker Container Testing for OAuth2

Test your OAuth2 changes using the **exact same container** that runs in ECS!

## 🚀 Quick Start

**Prerequisites**: Make sure your `.env` file contains the OAuth2 settings:
```bash
# Check your .env file has these:
grep -E "COGNITO_|API_V1_STR" .env
```

### Option 1: Simple Container Test (Fastest)
```bash
./docker-test.sh
```
- Builds container with your changes
- Runs with your .env file (same as production)
- Opens on http://localhost:8000/docs

### Option 2: Quick Rebuild (For Changes)
```bash
./docker-rebuild.sh
```
- Rebuilds and restarts container
- Perfect for testing code changes
- Faster than full docker-compose

### Option 3: Full Environment (With Database)
```bash
./docker-compose-test.sh
```
- Includes PostgreSQL and Redis
- Full production-like environment
- Takes longer to start

## 🧪 Testing OAuth2

1. **Start Container**: Run one of the scripts above
2. **Open Swagger UI**: http://localhost:8000/docs
3. **Test OAuth2 Modal**:
   - Click green "Authorize" button
   - Verify `client_id` shows the configured client ID
   - Check browser console for debug messages
4. **Debug Endpoint**: http://localhost:8000/debug/oauth-config (remove in production)

## 🔄 Development Workflow

1. **Make code changes** in `src/main.py`
2. **Rebuild container**: `./docker-rebuild.sh`
3. **Test immediately**: http://localhost:8000/docs
4. **Repeat** until OAuth2 works perfectly

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

## 🎯 What to Test

- **✅ Client ID**: Should show the configured client ID in modal
- **✅ OAuth2 Flow**: Modal opens and stays open
- **✅ Console Logs**: Debug messages show correct client_id
- **✅ Cognito Redirect**: Should redirect to `https://auth.mor.org`

## 🚨 Benefits of Container Testing

1. **Exact Same Environment**: Same as ECS production
2. **No Python Dependencies**: Container handles everything
3. **Fast Iterations**: Rebuild in ~30 seconds
4. **Real Production Config**: Uses actual Dockerfile
5. **No Deployment Wait**: Test locally instantly

## ⚙️ Environment Setup

### Check Your .env File
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
EOF
```

## 📁 Files Created

- `docker-test.sh` - Simple container test
- `docker-rebuild.sh` - Quick rebuild for changes  
- `docker-compose-test.sh` - Full environment test
- `DOCKER_TESTING.md` - This guide

## 🧹 Cleanup

```bash
# Remove test containers
docker stop morpheus-api-test && docker rm morpheus-api-test
docker-compose down

# Remove test image
docker rmi morpheus-api-test
```

Now you can test OAuth2 changes **instantly** without waiting for ECS deployments! 🎉
