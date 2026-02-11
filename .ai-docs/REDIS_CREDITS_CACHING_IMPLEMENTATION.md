# Credits Balance Caching & API Key Optimization - Implementation Guide

## Summary

This implementation addresses the database connection exhaustion issues identified during load testing. The primary bottleneck was **77% of database queries** were hitting the `credits_ledger` table (billing operations), with API key lookups and credits balance checks being completely uncached.

## Changes Made

### 1. **Credits Balance Caching** (High Impact - 77% of DB traffic)

**File**: `src/crud/credits.py`

**What Changed**:
- Added Redis caching to `get_or_create_balance()` with a **30-second TTL**
- Cache is bypassed when `for_update=True` (transactional writes) to ensure data consistency
- Cache invalidation on all balance update operations

**How It Works**:
```python
# Read path (cache hit)
1. Request comes in → Check Redis first
2. If cached (30s TTL) → Return balance from Redis (no DB query)
3. If cache miss → Query DB, cache result for 30s

# Write path (cache invalidation)
1. Balance updated → Delete Redis cache
2. Next read will cache fresh data from DB
```

**Impact**:
- **Every API request** checks credits balance for billing
- With caching: 1 DB query per user per 30 seconds (vs. 1 per request)
- For a user making 100 requests/minute: **Reduces from 100 DB queries to 2 DB queries**

**Files Modified**:
- `src/crud/credits.py`:
  - `get_or_create_balance()` - Added read-through cache
  - `update_balance()` - Added cache invalidation
  - `set_staking_daily_amount()` - Added cache invalidation
  - `set_allow_overage()` - Added cache invalidation
  - `reconcile_pending_holds()` - Added cache invalidation
  - `reconcile_all_balances()` - Added cache invalidation

**TTL Choice**:
- **30 seconds** is chosen to balance freshness vs. load reduction
- Short enough that balance updates appear "near real-time"
- Long enough to absorb burst traffic (100+ req/min from same user)

---

### 2. **API Key Caching Fix** (`get_current_api_key()`)

**File**: `src/dependencies.py`

**What Changed**:
- Added Redis caching to `get_current_api_key()` (was completely uncached)
- Uses same cache key structure as `get_api_key_user()` for consistency
- Background task for `last_used_at` updates (non-blocking)

**How It Works**:
```python
# Read path (cache hit)
1. Extract key_prefix from "sk-xxxxxx"
2. Check Redis cache for "api_key:{prefix}"
3. If cached → Validate hash, update last_used in background, return
4. If cache miss → Query DB, validate, cache for 5 minutes

# Cache invalidation
- When API key is deactivated (src/crud/api_key.py)
- When API key is deleted
```

**Impact**:
- **Every API request** validates the API key
- With caching: 1 DB query per API key per 5 minutes (vs. 1 per request)
- For 100 requests/minute with same API key: **Reduces from 100 DB queries to 1 DB query**

**Files Modified**:
- `src/dependencies.py`:
  - `get_current_api_key()` - Refactored to use Redis cache (mirrors `get_api_key_user()` pattern)

**TTL**:
- **300 seconds (5 minutes)** - same as existing API key cache
- Balances security (revoked keys expire quickly) vs. performance

---

## Testing Plan

### Phase 1: Code Changes (Do This First)

1. **Deploy Updated Code to TEST**:
   ```bash
   # Push code changes to test branch
   git add .
   git commit -m "Add credits balance caching and fix get_current_api_key()"
   git push origin test
   
   # Deploy via GitHub Actions or manual ECS update
   ```

2. **Verify Caching is Working**:
   ```bash
   # Check CloudWatch Logs for cache hit/miss events
   aws logs tail /ecs/dev-morpheus-api --follow --profile mor-org-prd
   
   # Look for log entries like:
   # "Credits balance cache hit" - Good!
   # "API key cache hit" - Good!
   ```

3. **Run Load Test**:
   ```bash
   # Use the same load test that previously exhausted connections
   # Monitor:
   # 1. Database connections (should stay well under max_connections)
   # 2. Cache hit rates (aim for >90% after warm-up)
   # 3. API response times (should improve)
   ```

4. **Monitor Metrics**:
   - **CloudWatch RDS Metrics**:
     - `DatabaseConnections` - Should be significantly lower
     - `ReadIOPS` - Should decrease
   - **CloudWatch Logs**:
     - Count cache hits vs. misses
   - **Redis Stats**:
     - Memory usage (should be minimal - just keys)
     - Hit rate percentage

### Phase 2: RDS Proxy (Only if Code Changes Aren't Enough)

5. **Enable RDS Proxy** (optional infrastructure-level boost):
   ```bash
   cd environments/03-morpheus_api/02-dev
   
   # Edit terraform.tfvars
   # Change: rds_proxy = false
   # To:     rds_proxy = true
   
   terragrunt apply
   ```

6. **Update DATABASE_URL** to use RDS Proxy:
   ```bash
   # Old: postgresql://user:pass@db.dev.mor.org:5432/dbname
   # New: postgresql://user:pass@dbproxy.dev.mor.org:5432/dbname
   
   # Update in Secrets Manager or ECS task definition
   ```

7. **Verify RDS Proxy is Working**:
   ```bash
   # Check RDS Proxy metrics in CloudWatch
   aws cloudwatch get-metric-statistics \
     --namespace AWS/RDS \
     --metric-name DatabaseConnections \
     --dimensions Name=DBProxyName,Value=dev-morpheus-api-rds-proxy \
     --start-time 2026-02-11T00:00:00Z \
     --end-time 2026-02-11T23:59:59Z \
     --period 300 \
     --statistics Average \
     --profile mor-org-prd
   ```

---

## Expected Results

### Without RDS Proxy (Code Changes Only)

**Before Caching**:
- 1000 requests/minute with single API key
- Database queries: ~2000/minute (API key + credits balance per request)
- DB connections: 94 used (pool_size + max_overflow)
- **Result**: Connection exhaustion at ~1000 req/min

**After Caching**:
- 1000 requests/minute with single API key
- Database queries: **~40/minute** (1 API key cache miss + 2 credits balance cache misses per minute)
- DB connections: **10-20 used** (only for actual cache misses)
- **Result**: Should handle 5000+ req/min without connection exhaustion

### With RDS Proxy (Optional Additional Layer)

**Additional Benefits**:
- Connection pooling at infrastructure level (shares connections across tasks)
- Query result caching (for identical SELECT queries)
- Automatic failover handling
- Lower connection overhead

**Expected**:
- DB connections: **5-10 used** (RDS Proxy pools connections aggressively)
- Can scale to **10+ ECS tasks** without hitting connection limits
- **Result**: Should handle 10,000+ req/min

---

## Cache Invalidation Strategy

### Credits Balance
**Invalidated On**:
- `update_balance()` - Any balance change (credits added, deducted, holds)
- `set_staking_daily_amount()` - Staking amount updated
- `set_allow_overage()` - Overage setting changed
- `reconcile_pending_holds()` - Balance reconciliation
- `reconcile_all_balances()` - Full balance reconciliation

**Why 30s TTL**:
- Balance changes are **write-heavy** during usage (every API call)
- 30s is short enough that users see updates quickly
- Long enough to absorb burst traffic (prevents cache stampede)

### API Keys
**Invalidated On**:
- `deactivate_api_key()` - Key is disabled
- `delete_all_user_api_keys()` - User's keys are deleted

**Why 5min TTL**:
- API keys change **rarely** (only when created/revoked)
- 5 minutes is acceptable for security (revoked keys expire quickly)
- Long TTL maximizes cache hit rate

---

## Rollback Plan

If caching causes issues:

1. **Disable Caching Without Code Changes**:
   ```bash
   # Set environment variable in ECS task definition
   CACHE_ENABLED=false
   
   # Redeploy service
   aws ecs update-service --service api --force-new-deployment --profile mor-org-prd
   ```

2. **Disable RDS Proxy**:
   ```bash
   # Edit terraform.tfvars
   rds_proxy = false
   
   # Update DATABASE_URL back to direct RDS endpoint
   # Apply terraform
   terragrunt apply
   ```

3. **Revert Code Changes**:
   ```bash
   git revert <commit-hash>
   git push origin test
   ```

---

## Cost Impact

### Application-Level Redis Caching
- **Cost**: $0 (uses existing ElastiCache Redis)
- **Benefit**: 95%+ reduction in database queries

### RDS Proxy (Optional)
- **Cost**: ~$0.015/hour * 730 hours = **$10.95/month**
- **Benefit**: Additional connection pooling + query caching
- **ROI**: Enables scaling to 10+ tasks without RDS upgrade ($50+/month)

---

## Monitoring & Alerts

### Key Metrics to Watch

1. **Redis Cache Hit Rate**:
   ```bash
   # CloudWatch Logs Insights query
   fields @timestamp, @message
   | filter @message like /cache hit/
   | stats count() as hits by bin(5m)
   ```

2. **Database Connections**:
   ```bash
   # RDS CloudWatch metric
   DatabaseConnections < 80 (healthy)
   DatabaseConnections > 90 (warning - approaching limit)
   ```

3. **API Response Times**:
   ```bash
   # Should improve with caching
   # CloudWatch Logs Insights query
   fields @timestamp, duration
   | filter endpoint = "/v1/chat/completions"
   | stats avg(duration) by bin(5m)
   ```

### Recommended Alerts

- **Alert**: RDS `DatabaseConnections` > 90% of `max_connections`
- **Alert**: Redis `CacheMisses` > 50% (indicates caching not working)
- **Alert**: API p99 latency > 2 seconds (performance degradation)

---

## Next Steps (Future Optimizations)

If database load is still high after these changes:

1. **Batch Credits Ledger Inserts**:
   - Current: 1 INSERT per API request
   - Future: Batch 10-100 inserts every 5-10 seconds
   - Impact: 90%+ reduction in write load

2. **Read Replicas**:
   - Add RDS read replica for reports/analytics queries
   - Route read-only queries to replica
   - Impact: Offload 50%+ of read traffic from primary

3. **Database Partitioning**:
   - Partition `credits_ledger` by month/quarter
   - Impact: Faster queries on historical data

---

## Conclusion

**Recommended Approach**:
1. ✅ **Deploy code changes first** (credits caching + API key fix)
2. ✅ **Test under load** - Should resolve connection exhaustion
3. ⏸️ **Hold on RDS Proxy** - Only add if code changes aren't sufficient
4. 📊 **Monitor for 24-48 hours** - Verify caching is working as expected

**Expected Outcome**:
- **95%+ reduction** in database queries for authentication/billing
- **10-20x capacity increase** (from ~1000 req/min to 10,000+ req/min)
- **Zero infrastructure cost** (uses existing Redis)
- **Quick rollback** if issues arise (set `CACHE_ENABLED=false`)

The code changes alone should solve the connection exhaustion problem. RDS Proxy is there as a safety net if needed, but the application-level caching is the real fix.
