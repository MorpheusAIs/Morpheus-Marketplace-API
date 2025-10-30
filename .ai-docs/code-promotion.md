# Morpheus Marketplace API - Code Promotion & CI/CD Pipeline

## 📋 PR Summary

Implemented automated CI/CD pipeline with semantic versioning, containerized deployments, and database migrations. The pipeline supports different promotion levels: DEV (test only), TEST (deploy to staging), MAIN (deploy to production), and feature branches (build/test only). All deployments use GHCR containers with automatic health checks and rollback capabilities.

For any questions, please contact nomadicrogue@mor.org or @NomadicRogue on GitHub

---

## 🚀 Code Promotion Process

### Branch Strategy & Automation

| Branch Type | Tag & Test | Build & Push | Deploy | Git Tag | Environment |
|-------------|------------|--------------|--------|---------|-------------|
| **feat/**, **fix/*** | ❌ | ❌ | ❌ | ❌ | Use Pull Requests → dev |
| **dev** | ✅ | ❌ | ❌ | ❌ | N/A |
| **test** | ✅ | ✅ | ✅ | ✅ | api.dev.mor.org |
| **main** | ✅ | ✅ + `:latest` | ✅ | ✅ | api.mor.org |


### 🏷️ Semantic Versioning

- **Development (dev)**: `v1.0.2-dev` (includes branch suffix)
- **Staging (test)**: `v1.0.3-test` (includes branch suffix)
- **Production (main)**: `v1.0.5` (clean semantic version)

## 🔄 Pipeline Stages

### 1. **Generate Semantic Version Tag**
- Runs on: All branches (dev, test, main, cicd/*)
- Generates semantic version based on Git history
- Creates unique container tags for each build

### 2. **Test Morpheus API**
- Runs on: All branches (dev, test, main, cicd/*)
- Executes unit tests with PostgreSQL test database
- Validates code quality and functionality
- Uploads coverage reports to Codecov

### 3. **Build & Push Docker Image**
- Runs on: test, main, cicd/* (NOT dev)
- Builds optimized Docker container with Poetry dependencies
- Uses Buildx caching for faster builds
- Pushes to GitHub Container Registry (GHCR)
- **Main branch**: Also tagged as `:latest`

### 4. **Deploy to AWS ECS**
- Runs on: test, main only
- Executes Alembic database migrations
- Updates ECS task definition with new container
- Performs rolling deployment with health checks
- **5-minute health check** validation
- **Automatic rollback** on deployment failure

### 5. **Create Git Tags**
- Runs on: test, main only
- Creates permanent Git tags for release tracking
- **Test**: Creates `v1.0.3-test` tags
- **Main**: Creates `v1.0.5` production tags

## 🛠️ Technical Implementation

### Container Strategy
- **Base Image**: Python 3.11-slim
- **Dependency Management**: Poetry with lock file validation
- **Multi-platform**: ARM64 + AMD64 for production, AMD64 for staging
- **Registry**: GitHub Container Registry (ghcr.io)

### Database Management
- **Migration Tool**: Alembic
- **Pre-deployment**: Captures current schema revision
- **Post-deployment**: Validates successful migration
- **Rollback**: Automatic database rollback on deployment failure

### Health Monitoring
- **Endpoint**: `/health` with version validation
- **Timeout**: 5-minute stabilization period
- **Validation**: Confirms semantic version in response
- **Circuit Breaker**: ECS deployment circuit breaker enabled

### Security & Access
- **AWS IAM**: Dedicated GitHub Actions user with minimal permissions
- **Secrets Management**: AWS Secrets Manager for database credentials
- **Container Security**: Non-root user execution

## 🌍 Environments

### Development (`api.dev.mor.org`)
- **Triggers**: Push to `test` branch
- **Database**: Dedicated RDS instance (dev)
- **Purpose**: Staging validation before production

### Production (`api.mor.org`)
- **Triggers**: Push to `main` branch
- **Database**: Dedicated RDS instance (prod)
- **Purpose**: Live production environment

## 🔧 Developer Workflow

### Standard Development Workflow
```bash
# 1. Create feature branch
git checkout -b feat/my-new-feature

# 2. Develop and commit changes (no CI/CD triggers)
git add .
git commit -m "Add new feature"
git push origin feat/my-new-feature

# 3. Create Pull Request to dev branch
# PR Review → Merge to dev triggers: Tag → Test only

# 4. Promote to staging
git checkout test
git merge dev
git push origin test  # Triggers: Tag → Test → Build → Deploy → Git Tag

# 5. Promote to production
git checkout main
git merge test
git push origin main  # Triggers: Tag → Test → Build → Deploy → Git Tag + :latest
```


### Container Tags
- **Development**: `ghcr.io/morpheusais/morpheus-marketplace-api:v1.0.2-dev`
- **Staging**: `ghcr.io/morpheusais/morpheus-marketplace-api:v1.0.3-test`
- **Production**: `ghcr.io/morpheusais/morpheus-marketplace-api:v1.0.5` + `:latest`

## 📊 Monitoring & Validation

### Health Check Response
```json
{
  "status": "ok",
  "timestamp": "2025-09-12T13:45:23.627555",
  "version": "v1.0.5-main",
  "database": "healthy",
  "container": {
    "id": "9f2a5cd8-91fa-458f-9715-e060367c5ff6",
    "system": "Linux-5.10.240-238.959.amzn2.x86_64",
    "python_version": "3.11.13"
  },
  "uptime": {
    "seconds": 161885,
    "human_readable": "1d 20h 58m 5s",
    "started_at": "2025-09-09T21:04:19.272538"
  }
}
```

### Rollback Strategy
- **ECS Circuit Breaker**: Automatic service rollback on deployment failure
- **Database Rollback**: Automatic Alembic downgrade on deployment failure
- **Manual Rollback**: Previous container versions available in GHCR
- **Git Rollback**: Tagged releases for easy version identification

---

*This pipeline ensures reliable, automated deployments with comprehensive testing, monitoring, and rollback capabilities across all environments.*
