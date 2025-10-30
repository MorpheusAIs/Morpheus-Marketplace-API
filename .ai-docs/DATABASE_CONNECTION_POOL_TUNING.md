# Database Connection Pool Tuning Guide

## Problem Overview

During rapid-fire load testing against the API Gateway, you encountered this error:

```
HTTP 500: {"detail":"Unexpected error validating API key: QueuePool limit of size 20 overflow 30 reached, connection timed out, timeout 30.00"}
```

This is a **SQLAlchemy connection pool exhaustion error**, not an RDS configuration issue. The error indicates that all 50 available connections (20 base pool + 30 overflow) were in use, and a new request timed out waiting for an available connection.

## Root Cause

The issue occurs in the **API application layer** (not RDS or Terraform). SQLAlchemy manages database connections through a connection pool, and the pool settings were **hardcoded** in the application code with values too low for high-concurrency scenarios.

## Solution Implemented

We've made the SQLAlchemy connection pool settings **configurable via environment variables** at three levels:

1. **Application Code** - Added environment variable support
2. **Docker/Local Development** - Added settings to `.env.example`
3. **ECS Task Definition** - Added Terraform variables for deployment
4. **RDS Configuration** - Added custom parameter group for `max_connections` tuning

---

## Changes Made

### 1. Application Code Changes

#### `src/core/config.py`
Added new configuration settings:

```python
# SQLAlchemy Connection Pool Settings
DB_POOL_SIZE: int = Field(default=int(os.getenv("DB_POOL_SIZE", "20")))
DB_MAX_OVERFLOW: int = Field(default=int(os.getenv("DB_MAX_OVERFLOW", "30")))
DB_POOL_TIMEOUT: int = Field(default=int(os.getenv("DB_POOL_TIMEOUT", "30")))
DB_POOL_RECYCLE: int = Field(default=int(os.getenv("DB_POOL_RECYCLE", "3600")))
DB_POOL_PRE_PING: bool = Field(default=os.getenv("DB_POOL_PRE_PING", "true").lower() == "true")
```

#### `src/db/database.py`
Updated the SQLAlchemy engine to use settings from config:

```python
engine = create_async_engine(
    str(settings.DATABASE_URL),
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    echo=False,
    pool_size=settings.DB_POOL_SIZE,              # Now configurable via DB_POOL_SIZE
    max_overflow=settings.DB_MAX_OVERFLOW,        # Now configurable via DB_MAX_OVERFLOW
    pool_timeout=settings.DB_POOL_TIMEOUT,        # Now configurable via DB_POOL_TIMEOUT
    pool_recycle=settings.DB_POOL_RECYCLE,        # Now configurable via DB_POOL_RECYCLE
    pool_reset_on_return='rollback',
)
```

#### `env.example`
Added documentation and default values:

```bash
# SQLAlchemy Connection Pool Settings
DB_POOL_SIZE=20                    # Base connection pool size
DB_MAX_OVERFLOW=30                 # Additional connections during load spikes
DB_POOL_TIMEOUT=30                 # Seconds to wait for connection before timeout
DB_POOL_RECYCLE=3600               # Seconds before recycling connections (1 hour)
DB_POOL_PRE_PING=true              # Test connections before use
```

### 2. Terraform/Infrastructure Changes

#### `environments/03-morpheus_api/.terragrunt/04_api_service.tf`
Added environment variables to ECS task definition:

```hcl
environment = [
  {
    name = "BASE_URL"
    value = local.api_base_url
  },
  {
    name = "DB_POOL_SIZE"
    value = tostring(lookup(var.api_service, "db_pool_size", 20))
  },
  {
    name = "DB_MAX_OVERFLOW"
    value = tostring(lookup(var.api_service, "db_max_overflow", 30))
  },
  # ... additional pool settings
]
```

#### `environments/03-morpheus_api/.terragrunt/02_rds.tf`
Added custom parameter group for RDS `max_connections` tuning:

```hcl
resource "aws_db_parameter_group" "morpheus_api" {
  name        = "${var.env_lifecycle}-morpheus-api-pg15"
  family      = "postgres15"
  description = "Custom parameter group for Morpheus API with tuned connection limits"
  
  parameter {
    name  = "max_connections"
    value = lookup(var.rds_postgres, "max_connections", "LEAST({DBInstanceClassMemory/9531392},5000)")
    apply_method = "pending-reboot"  # Requires reboot to take effect
  }
}
```

#### Environment-Specific Configurations

**Development (`02-dev/terraform.tfvars`):**
```hcl
api_service = {
  # ... existing settings ...
  
  # Tuned for load testing with 3 max tasks
  db_pool_size           = 40
  db_max_overflow        = 60
  db_pool_timeout        = 30
  db_pool_recycle        = 3600
  db_pool_pre_ping       = true
}

rds_postgres = {
  # ... existing settings ...
  max_connections = "350"  # 3 tasks * (40 + 60) + 50 buffer
}
```

**Production (`04-prd/terraform.tfvars`):**
```hcl
api_service = {
  # ... existing settings ...
  
  # Conservative production settings with 10 max tasks
  db_pool_size           = 30
  db_max_overflow        = 50
  db_pool_timeout        = 30
  db_pool_recycle        = 3600
  db_pool_pre_ping       = true
}

rds_postgres = {
  # ... existing settings ...
  max_connections = "1000"  # 10 tasks * (30 + 50) + 200 buffer
}
```

---

## Configuration Parameters Explained

### `DB_POOL_SIZE` (default: 20)
- **Base connection pool size** maintained per application instance
- These connections are always available and reused
- Higher values = faster response times but more resource usage

### `DB_MAX_OVERFLOW` (default: 30)
- **Additional connections** created during load spikes
- Total possible connections = `DB_POOL_SIZE + DB_MAX_OVERFLOW`
- These connections are created on-demand and closed when not needed

### `DB_POOL_TIMEOUT` (default: 30)
- **Seconds to wait** for an available connection before timing out
- If all connections are busy for this duration, the request fails with the error you saw
- Lower values fail faster, higher values provide more patience

### `DB_POOL_RECYCLE` (default: 3600)
- **Seconds before recycling** a connection (1 hour default)
- Prevents stale connections and server-side timeout issues
- Should be less than the database's connection timeout

### `DB_POOL_PRE_PING` (default: true)
- **Test connections** before using them
- Adds slight overhead but prevents errors from stale connections
- Recommended for production environments

---

## Capacity Planning Formula

Calculate required RDS `max_connections`:

```
max_connections = (num_instances × (pool_size + max_overflow)) + buffer

Example for dev (3 ECS tasks):
max_connections = (3 × (40 + 60)) + 50 = 350

Example for production (10 ECS tasks):
max_connections = (10 × (30 + 50)) + 200 = 1000
```

### RDS Instance Class Connection Limits

Default `max_connections` by instance class:

| Instance Class | Default max_connections | Memory (GB) |
|---------------|------------------------|-------------|
| db.t3.micro   | ~81                    | 1           |
| db.t3.small   | ~167                   | 2           |
| db.t3.medium  | ~334                   | 4           |
| db.t3.large   | ~668                   | 8           |
| db.m5.large   | ~422                   | 8           |
| db.m5.xlarge  | ~858                   | 16          |
| db.m5.2xlarge | ~1729                  | 32          |

**Important:** If you set `max_connections` higher than the instance class default, you'll need to ensure the instance has sufficient memory. Each connection consumes approximately 10-15MB of RAM.

---

## Deployment Steps

### For Development Environment

1. **Update the API application** (if not using the latest code):
   ```bash
   cd /path/to/Morpheus-Marketplace-API
   git pull
   # Rebuild and redeploy the Docker image
   ```

2. **Apply Terraform changes**:
   ```bash
   cd /path/to/Morpheus-Infra/environments/03-morpheus_api/02-dev
   terraform plan
   terraform apply
   ```

3. **Reboot RDS instance** (required for `max_connections` change):
   ```bash
   aws rds reboot-db-instance --db-instance-identifier rds-dev-morpheus-api --profile mor-org-prd
   ```

4. **Restart ECS tasks** to pick up new environment variables:
   ```bash
   aws ecs update-service \
     --cluster ecs-dev-morpheus-engine \
     --service svc-dev-api-service \
     --force-new-deployment \
     --profile mor-org-prd \
     --region us-east-2
   ```

### For Production Environment

Follow the same steps but use the production paths and identifiers:
- Path: `environments/03-morpheus_api/04-prd`
- RDS instance: `rds-prd-morpheus-api`
- ECS cluster: `ecs-prd-morpheus-engine`
- ECS service: `svc-prd-api-service`

---

## Tuning Recommendations

### Start Conservative
Begin with default values and increase gradually based on monitoring:
- `DB_POOL_SIZE=20`
- `DB_MAX_OVERFLOW=30`

### For Load Testing
Use higher values to handle burst traffic:
- `DB_POOL_SIZE=40-50`
- `DB_MAX_OVERFLOW=50-100`

### For Production
Size based on expected concurrent requests:
- Calculate: `concurrent_requests_per_task × average_request_duration_seconds / connection_usage_per_request`
- Add 20-30% buffer for overhead
- Monitor and adjust based on metrics

### Warning Signs
- **Frequent timeouts**: Increase pool size or max_overflow
- **High database CPU**: Decrease pool size (too many connections)
- **Memory pressure on RDS**: Decrease max_connections or upgrade instance
- **Slow queries**: Connection count may not be the issue; check query performance

---

## Monitoring Recommendations

### Application Metrics (CloudWatch/Logs)
- Connection pool size usage
- Connection checkout time
- Pool timeout errors
- Query execution time

### RDS Metrics (CloudWatch)
- `DatabaseConnections` - Current active connections
- `CPUUtilization` - Should remain < 80%
- `FreeableMemory` - Ensure sufficient for connections
- `ReadLatency` / `WriteLatency` - Query performance

### Key Metrics to Watch
1. **Active Connections**: Should be < 80% of `max_connections`
2. **Pool Exhaustion**: Monitor timeout errors in application logs
3. **Database CPU**: High CPU with many connections indicates query optimization needed
4. **Connection Lifetime**: Ensure connections recycle properly

---

## Troubleshooting

### Still Getting Timeout Errors?

1. **Check ECS task count**: Are all tasks running?
   ```bash
   aws ecs describe-services --cluster ecs-dev-morpheus-engine --services svc-dev-api-service --region us-east-2 --profile mor-org-prd
   ```

2. **Verify environment variables**:
   ```bash
   aws ecs describe-task-definition --task-definition tsk-dev-api-service --region us-east-2 --profile mor-org-prd
   ```

3. **Check RDS connections**:
   ```sql
   SELECT count(*) FROM pg_stat_activity;
   SELECT max_connections FROM pg_settings WHERE name = 'max_connections';
   ```

4. **Review CloudWatch logs** for connection pool metrics

### RDS Connection Limit Exceeded

If you see `FATAL: sorry, too many clients already` errors:

1. **Increase `max_connections` in Terraform**
2. **Reboot RDS instance** for the change to take effect
3. **Consider upgrading RDS instance class** if memory is constrained

### High Database CPU with Many Connections

This indicates query performance issues, not connection pool issues:

1. **Enable Performance Insights** on RDS
2. **Review slow query logs**
3. **Add missing indexes**
4. **Optimize query patterns**

---

## Rollback Plan

If these changes cause issues:

1. **Revert Terraform**:
   ```bash
   cd /path/to/Morpheus-Infra/environments/03-morpheus_api/02-dev
   git revert HEAD
   terraform apply
   ```

2. **Use default parameter group**:
   ```hcl
   parameter_group_name = "default.postgres15"
   ```

3. **Remove environment variables** from ECS task definition and restart tasks

---

## Summary

**Location of the Issue**: Application layer (SQLAlchemy connection pool)  
**Not in**: RDS or Terraform (though we tuned both for better support)

**What was changed**:
1. ✅ Made connection pool settings configurable via environment variables
2. ✅ Added Terraform variables for ECS task definitions
3. ✅ Created custom RDS parameter group for `max_connections`
4. ✅ Configured dev environment for load testing (40 pool + 60 overflow = 100 per task)
5. ✅ Configured production with conservative settings (30 pool + 50 overflow = 80 per task)

**Next steps**:
1. Deploy the updated application code
2. Apply Terraform changes
3. Reboot RDS to apply `max_connections` change
4. Run load tests again with new settings
5. Monitor CloudWatch metrics and adjust as needed

**For future reference**: Always ensure `RDS max_connections > (num_tasks × (pool_size + max_overflow) + buffer)`

