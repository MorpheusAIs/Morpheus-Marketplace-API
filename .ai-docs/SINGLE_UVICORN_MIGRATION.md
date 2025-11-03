# Single Uvicorn Per Task Migration

## Overview

This document describes the migration from **Gunicorn with 4 workers** to **single Uvicorn per ECS task**, optimized for ECS/Fargate deployment with ALB-based load balancing.

## Architecture Comparison

### Previous Architecture (Gunicorn Multi-Worker)

```
ECS Task A
├── Gunicorn Master
│   ├── Uvicorn Worker 1
│   ├── Uvicorn Worker 2
│   ├── Uvicorn Worker 3
│   └── Uvicorn Worker 4
│
ALB (with stickiness)
├── Routes to: Task A
│   └── Gunicorn internally load-balances to Worker 1-4
│       └── Session may be on any worker
│           └── Requires database lookup more often
```

**Issues:**
- ALB stickiness routes to **task**, not to **specific worker**
- Internal Gunicorn load balancing means requests from same user hit different workers
- Session caching less effective (each worker needs to cache independently)
- More memory overhead (4 workers × session cache × database connections)

### New Architecture (Single Uvicorn)

```
ECS Task A              ECS Task B              ECS Task C
└── Uvicorn 1           └── Uvicorn 1           └── Uvicorn 1
    (single process)        (single process)        (single process)
          ↑                       ↑                       ↑
          └───────────────────────┴───────────────────────┘
                                  │
                            ALB (with stickiness)
                                  │
                            User Request
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
              First Request              Subsequent Requests
              (no cookie)                (with AWSALB cookie)
                    │                           │
              Round-robin to Task A       Always to Task A
                    │                           │
              Sets AWSALB cookie          Reuses session
```

**Benefits:**
- ✅ **Perfect stickiness**: ALB → Task = specific process
- ✅ **Better session locality**: Same process always handles same user
- ✅ **Simpler architecture**: One process per container
- ✅ **Lower memory per task**: Single process, single cache
- ✅ **Easier debugging**: One process per task = simpler logs
- ✅ **Better resource isolation**: Task failure doesn't affect other processes
- ✅ **Horizontal scaling**: ECS scales by adding more tasks

## What Changed

### 1. Dockerfile

**Before:**
```dockerfile
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", "-b", "0.0.0.0:8000", ...]
```

**After:**
```dockerfile
CMD ["uvicorn", "src.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--timeout-keep-alive", "75", \
     "--timeout-graceful-shutdown", "30", \
     "--limit-concurrency", "1000", \
     "--backlog", "2048", \
     "--no-access-log"]
```

### 2. Uvicorn Configuration Explained

| Flag | Value | Purpose |
|------|-------|---------|
| `--host` | `0.0.0.0` | Listen on all interfaces (required in container) |
| `--port` | `8000` | Application port |
| `--timeout-keep-alive` | `75` | Keep-alive timeout (matches ALB's 75s) |
| `--timeout-graceful-shutdown` | `30` | Graceful shutdown timeout for ECS task draining |
| `--limit-concurrency` | `1000` | Max concurrent connections per task |
| `--backlog` | `2048` | OS socket backlog for queued connections |
| `--no-access-log` | - | Disable access logs (we use structured logging) |

### 3. Why These Timeout Values?

```
ALB Idle Timeout:          300 seconds (5 minutes)
ALB Keep-Alive:            Default ~60s, set to 75s
Uvicorn Keep-Alive:        75 seconds (matches ALB)
Uvicorn Graceful Shutdown: 30 seconds (for ECS draining)
HTTP Client Timeout:       180 seconds (for proxy router)
Proxy Router Request:      180 seconds (for large tokens)
```

**Flow:**
1. User makes request → ALB → Task A
2. Request takes up to 180s to complete (proxy router timeout)
3. Connection kept alive for 75s between requests (keep-alive)
4. ALB waits up to 300s for response (idle timeout)
5. On task shutdown, 30s to finish in-flight requests (graceful shutdown)

## Scaling Configuration

### Current Setup (4 workers per task)

```
Desired Tasks: 2
Total Workers: 2 tasks × 4 workers = 8 concurrent workers
```

### New Setup (1 worker per task)

To maintain **same capacity**, you need **4 tasks**:

```
Desired Tasks: 4
Total Workers: 4 tasks × 1 worker = 4 concurrent workers
```

**However**, with better resource isolation, you may need **fewer tasks** than expected:
- Single uvicorn is more efficient (less overhead)
- Async I/O handles many concurrent requests per process
- Session caching more effective

### Recommended Starting Point

| Environment | Min Tasks | Desired Tasks | Max Tasks |
|-------------|-----------|---------------|-----------|
| Dev         | 1         | 2             | 4         |
| Staging     | 2         | 3             | 6         |
| Production  | 3         | 4             | 10        |

### Terraform Configuration

Update `Morpheus-Infra/environments/03-morpheus_api/02-dev/terraform.tfvars`:

```hcl
# Before (with 4 workers per task)
ecs_service_api = {
  desired_count = 2  # 2 tasks × 4 workers = 8 total
  min_count     = 1
  max_count     = 4
  cpu           = 1024  # 1 vCPU shared across 4 workers
  memory        = 2048  # 2 GB shared across 4 workers
}

# After (with 1 worker per task)
ecs_service_api = {
  desired_count = 4    # 4 tasks × 1 worker = 4 total (more efficient)
  min_count     = 2    # Always keep 2 running
  max_count     = 10   # Scale up to 10 for traffic spikes
  cpu           = 512  # 0.5 vCPU per task (less overhead)
  memory        = 1024 # 1 GB per task (single process uses less)
}
```

**Note:** Single uvicorn uses **less CPU/memory per task** because:
- No Gunicorn master process overhead
- No inter-process communication
- Single Python interpreter
- More efficient memory usage

### Auto-Scaling Considerations

With single uvicorn per task, auto-scaling becomes **more effective**:

```hcl
# CPU-based scaling (responds to actual load)
cpu_target_value = 60  # Scale up when CPU > 60%

# Memory-based scaling (prevents OOM)
memory_target_value = 70  # Scale up when memory > 70%

# Request-based scaling (best for API workloads)
request_count_per_target = 1000  # Scale when requests > 1000/task
```

**Recommendation:** Start with **request count** scaling for APIs:
- More predictable than CPU/memory
- Responds to actual user load
- Works well with ALB target groups

## Migration Steps

### 1. Build and Test Locally

```bash
# Build new image
docker build -t morpheus-api:single-uvicorn .

# Test locally
docker run -p 8000:8000 --env-file .env morpheus-api:single-uvicorn

# Verify single process
docker exec <container-id> ps aux
# Should show: uvicorn src.main:app (ONE process)
```

### 2. Deploy to Dev Environment

```bash
# Push branch
git push origin feature/single-uvicorn-per-task

# GitHub Actions will:
# 1. Build Docker image
# 2. Push to ECR
# 3. Update ECS task definition
# 4. Deploy to ECS

# Monitor deployment
aws ecs describe-services \
  --cluster ecs-dev-morpheus-engine \
  --services svc-dev-api-service
```

### 3. Update Terraform (Task Count)

```bash
cd Morpheus-Infra/environments/03-morpheus_api/02-dev

# Update terraform.tfvars (desired_count, cpu, memory)
vim terraform.tfvars

# Plan changes
terragrunt plan

# Apply (increase task count)
terragrunt apply
```

### 4. Monitor Performance

```bash
# CloudWatch Metrics to watch:
# - CPUUtilization (per task)
# - MemoryUtilization (per task)
# - TargetResponseTime (ALB)
# - RequestCountPerTarget (ALB)
# - HealthyHostCount (ALB)

# ECS Service Events
aws ecs describe-services \
  --cluster ecs-dev-morpheus-engine \
  --services svc-dev-api-service \
  --query 'services[0].events[:10]'

# CloudWatch Logs
aws logs tail /aws/ecs/ecs-dev-morpheus-engine/svc-dev-api-service --follow
```

### 5. Test Stickiness

```bash
# Make multiple requests with cookie persistence
curl -v https://api.dev.mor.org/health \
  -c cookies.txt \
  -b cookies.txt

# Verify AWSALB cookie is set and reused
# All requests should hit the same task (check task ID in logs)
```

## Performance Comparison

### Expected Improvements

| Metric | Before (4 workers) | After (1 worker) | Change |
|--------|-------------------|------------------|---------|
| Memory per task | ~800 MB | ~400 MB | -50% |
| CPU per task | ~30% | ~20% | -33% |
| Session cache hits | ~60% | ~85% | +42% |
| Request latency (cached) | ~150ms | ~100ms | -33% |
| Request latency (uncached) | ~200ms | ~200ms | Same |
| Connection overhead | Higher | Lower | Better |

### Testing Checklist

- [ ] Health endpoint responds: `GET /health`
- [ ] API docs load: `GET /api/v1/docs`
- [ ] Chat completions work: `POST /api/v1/chat/completions`
- [ ] Large token requests (4K tokens): ~50s response time
- [ ] 12 sequential requests: All complete successfully
- [ ] ALB stickiness verified: Same task handles user's requests
- [ ] Auto-scaling works: New tasks spawn under load
- [ ] Graceful shutdown: In-flight requests complete before task stops

## Rollback Plan

If issues arise, rollback is simple:

### Option 1: Revert Docker Image Tag

```bash
# In ECS console or via CLI
aws ecs update-service \
  --cluster ecs-dev-morpheus-engine \
  --service svc-dev-api-service \
  --force-new-deployment \
  --task-definition <previous-task-definition-arn>
```

### Option 2: Revert Git Branch

```bash
# Merge the previous working branch
git checkout dev
git merge origin/dev
git push origin dev

# GitHub Actions will auto-deploy previous version
```

### Option 3: Scale Up Tasks Temporarily

```bash
# If performance issue, just add more tasks
terragrunt apply -var='ecs_service_api.desired_count=8'
```

## FAQ

### Q: Why not use Gunicorn's gevent workers?

**A:** We're using FastAPI with async/await, which requires ASGI (Uvicorn). Gevent is for WSGI (Flask/Django).

### Q: Can I still use multiple workers?

**A:** Yes, uvicorn supports `--workers N`, but it's better to let ECS scale tasks horizontally. Single worker per task provides:
- Better isolation
- Simpler architecture
- More effective ALB stickiness
- Easier debugging

### Q: What about WebSocket support?

**A:** Uvicorn has excellent WebSocket support. ALB also supports WebSockets with connection upgrades. Stickiness ensures WebSocket connections stay on the same task.

### Q: How many concurrent requests can one uvicorn handle?

**A:** With async/await, one uvicorn can handle **hundreds** of concurrent requests:
- CPU-bound: ~50-100 concurrent
- I/O-bound (like our API): ~500-1000 concurrent
- `--limit-concurrency 1000` prevents overload

### Q: Should I use `--reload` in production?

**A:** **NO!** `--reload` is for development only. It watches for file changes and auto-restarts, which:
- Increases CPU usage
- Introduces instability
- Breaks ALB health checks
- Is unnecessary (ECS handles deployments)

## Additional Resources

- [Uvicorn Deployment Docs](https://www.uvicorn.org/deployment/)
- [FastAPI Deployment](https://fastapi.tiangolo.com/deployment/)
- [AWS ECS Best Practices](https://docs.aws.amazon.com/AmazonECS/latest/bestpracticesguide/)
- [ALB Target Group Stickiness](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/sticky-sessions.html)

## Summary

**This migration provides:**
- ✅ Simpler architecture (1 process per container)
- ✅ Better ALB stickiness (ALB → Task = specific process)
- ✅ More efficient resource usage (less overhead)
- ✅ Easier horizontal scaling (ECS handles it)
- ✅ Better debugging (clearer logs)
- ✅ Lower cost (smaller tasks, more efficient packing)

**Key takeaway:** Let ECS/Fargate handle horizontal scaling. Keep containers simple and stateless. Let ALB handle load balancing and stickiness.

