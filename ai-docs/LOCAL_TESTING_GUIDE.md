# Local Testing Guide

This guide explains how to test the Morpheus API locally using external services (dev database, Cognito) for fast development cycles.

## Overview

The local testing setup allows you to:
- Run the API locally in Docker with hot-reload
- Use the external dev PostgreSQL database
- Use the external dev Cognito for authentication
- Use the external proxy-router for model routing
- Test API changes quickly without full deployment

## Benefits

✅ **Fast Cycle Testing** - No need to deploy to ECS for every change
✅ **Real Environment** - Uses actual dev database and services
✅ **Hot Reload** - Code changes reflect immediately
✅ **Full API Testing** - All endpoints work with real data
✅ **Safe Testing** - Isolated from production, uses dev environment

## Setup

### 1. Create Local Environment File

```bash
# Copy the example file
cp env.local.example .env.local

# Edit with your dev environment values
nano .env.local
```

### 2. Fill in Required Values

You'll need these values from the dev environment:

```bash
# Database connection to dev environment
DATABASE_URL=postgresql+asyncpg://username:password@db.dev.mor.org:5432/database_name

# Cognito settings from dev environment
AWS_COGNITO_USER_POOL_ID=us-east-1_xxxxxxxxx
AWS_COGNITO_CLIENT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxx
AWS_COGNITO_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# JWT secret (can use dev value or generate new)
JWT_SECRET_KEY=your-jwt-secret-key-here
```

### 3. Start Local Testing

```bash
# Simple method - use the script
./scripts/test_local.sh

# Manual method - direct docker-compose
docker-compose -f docker-compose.local.yml up --build
```

## Testing Workflow

### 1. Start Local Environment
```bash
./scripts/test_local.sh
```

### 2. API Available At
- **Base URL:** http://localhost:8000
- **Health Check:** http://localhost:8000/health
- **API Docs:** http://localhost:8000/docs

### 3. Run Tests
```bash
# API integration tests (in another terminal)
./tests/api/test_chat_curl.sh sk-YOUR-DEV-API-KEY

# Or run specific test scripts
cd tests/api
./test_chat_auth_consistency.sh
```

### 4. Make Changes
- Edit code in `src/`
- Changes auto-reload in container
- Test immediately at http://localhost:8000

### 5. Stop Testing
```bash
# Ctrl+C in terminal, or:
docker-compose -f docker-compose.local.yml down
```

## What's Included

### ✅ Services Connected
- **Database:** External dev PostgreSQL
- **Authentication:** External dev Cognito
- **Proxy Router:** External dev router
- **Model Service:** CloudFront (same as production)

### ✅ Features Working
- All API endpoints
- Authentication (API keys, JWT)
- Database operations
- Model routing
- Chat storage
- Real-time model fetching

### ✅ Development Features
- **Hot Reload:** Code changes apply immediately
- **Volume Mounts:** Source code mounted for editing
- **Debug Logs:** Full logging available
- **Health Checks:** Container health monitoring

## Troubleshooting

### Container Won't Start
```bash
# Check logs
docker-compose -f docker-compose.local.yml logs

# Check environment variables
docker-compose -f docker-compose.local.yml config
```

### Database Connection Issues
```bash
# Test database connectivity
docker run --rm postgres:15-alpine pg_isready -h db.dev.mor.org -p 5432

# Check DATABASE_URL format
echo $DATABASE_URL
```

### Authentication Issues
```bash
# Verify Cognito settings
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"test"}'
```

### Port Conflicts
```bash
# Check what's using port 8000
lsof -i :8000

# Use different port
docker-compose -f docker-compose.local.yml up -p 8001:8000
```

## Comparison with Full Deployment

| Aspect | Local Testing | Full Deployment |
|--------|---------------|-----------------|
| **Speed** | ~30 seconds | ~10-15 minutes |
| **Database** | External dev DB | External dev/prd DB |
| **Authentication** | External dev Cognito | External dev/prd Cognito |
| **Hot Reload** | ✅ Yes | ❌ No |
| **Real Data** | ✅ Yes (dev) | ✅ Yes |
| **CI/CD Pipeline** | ❌ Skipped | ✅ Full pipeline |
| **ECS Health Checks** | ❌ Local only | ✅ Full monitoring |

## Best Practices

### 1. Use for Development
- ✅ Testing new features
- ✅ Debugging issues
- ✅ API endpoint testing
- ✅ Quick iterations

### 2. Don't Use for
- ❌ Performance testing (use ECS)
- ❌ Production testing
- ❌ Load testing
- ❌ Final integration testing

### 3. Development Workflow
1. **Develop locally** with hot reload
2. **Test with local setup** using real dev services
3. **Commit and push** when ready
4. **Deploy via CI/CD** for final testing
5. **Promote to production** when stable

## Security Notes

⚠️ **Important:**
- Uses dev environment credentials
- Never use production credentials locally
- `.env.local` should be in `.gitignore`
- Don't commit sensitive values

## Integration with Existing Tests

The local setup works perfectly with existing test scripts:

```bash
# Start local environment
./scripts/test_local.sh

# In another terminal, run tests
./tests/api/test_chat_curl.sh sk-YOUR-DEV-API-KEY
```

This provides the **best of both worlds**: fast local development with real environment testing!
