# Model Switch Rate Limiting - CICD Deployment & Backout Plan

## Deployment Overview

**Approach**: Terraform first, then CICD pipeline handles application deployment.

```
┌────────────────────────────────────────────────────────────────┐
│  DEPLOYMENT SEQUENCE                                           │
└────────────────────────────────────────────────────────────────┘

1. Commit changes to fix branch
2. Apply Terraform (infrastructure first)
3. Merge to main (triggers CICD)
4. CICD handles:
   - Docker build
   - Database migration (alembic upgrade head)
   - ECS deployment
   - Health checks
```

---

## Phase 1: Infrastructure Changes (Terraform)

### Step 1: Commit Infra Changes

### Step 2: Apply Terraform to Production (04-prd)

### Step 3: Apply Terraform to Dev (02-dev) - Optional

---

## Phase 2: Application Deployment (CICD Pipeline)

### Step 1: Commit Application Changes
```bash
git checkout -b hotfix/model-switch-rate-limiting
git commit -m "hotfix: Add model switch rate limiting
git push origin hotfix/model-switch-rate-limiting
```
### Step 2: Create Pull Request to Main

### Step 3: CICD Pipeline Executes Automatically

**GitHub Actions will**:
1. ✅ Build Docker image
2. ✅ Run alembic upgrade head (creates table)
3. ✅ Push to ECR
4. ✅ Update ECS service
5. ✅ Run health checks
6. ✅ Complete deployment

### Step 4: Verify Deployment (Rate Limiting Still Disabled)
```bash
# Check logs for successful startup
aws logs tail /aws/ecs/services/prd/morpheus-api \
  --follow \
  --region us-east-2 \
  --profile mor-org-prd \
  | grep -E "(rate_limit|startup|migration)"

# Expected logs:
# - "Alembic migration successful" or similar
# - "Model switch rate limiting is disabled system-wide"
# - No errors
```

### Step 5: Verify Table Created
```sql
-- Connect to production database
psql "CONNECTIONSTRINGHERE"
-- Check table exists
\d api_key_model_switches

-- Should show table structure
-- Count should be 0 (not in use yet)
SELECT COUNT(*) FROM api_key_model_switches;
```

### Step 6: Monitor for 24 Hours (Disabled State)
- [ ] Service healthy
- [ ] No errors in logs
- [ ] API responding normally
- [ ] No behavior changes
- [ ] Table exists but empty

---

## Phase 3: Enable Rate Limiting (After 24h)

### Step 1: Update Terraform Variables
### Step 2: Apply Terraform

### Step 3: Verify Rate Limiting Active
```sql
-- Check database for recorded switches
SELECT COUNT(*) FROM api_key_model_switches;
-- Should be > 0 and growing

SELECT 
  COUNT(*) as switches_last_hour
FROM api_key_model_switches
WHERE user_id = 3 
AND switched_at >= NOW() - INTERVAL '1 hour';
-- Should cap at 10
```

---

## 🔄 BACKOUT PLAN (If Issues Occur)

### Option 1: Quick Disable (No Code Changes)

**Best for**: Rate limiting causing issues but code is otherwise fine.
# Step 1: Disable via terraform (update var, plan apply to restart service)
# Step 2: Verify disabled
# Step 3: Monitor for 24 hours
**Impact**:
- ✅ Service returns to normal immediately
- ✅ No code changes needed
- ✅ Table remains (no harm - just unused)
- ✅ Can re-enable anytime
- ✅ Takes ~5 minutes (Terraform apply + ECS restart)

**Database State**:
- Table `api_key_model_switches` remains in database
- Records remain (for analysis)
- No impact on application
- Can be dropped manually later if desired

---

### Option 2: Full Code Rollback

**Best for**: Code has bugs, service issues, or unexpected behavior.

```bash
# Step 1: Revert the commit
git checkout main
git revert <commit-hash>  # Revert the hotfix commit

# Or reset to previous commit
git reset --hard HEAD~1  # Only if not pushed yet

# Step 2: Push revert
git push origin main

# Step 3: CICD deploys old code automatically
# Monitor GitHub Actions for completion
```

**What CICD Does**:
- ✅ Builds Docker image with old code
- ✅ Deploys to ECS
- ⚠️ Does NOT run database migration rollback
- ✅ Service runs old code (no rate limiting)

**Database State After Rollback**:
- ❗ Table `api_key_model_switches` REMAINS in database
- ❗ Old code doesn't know about this table
- ✅ This is SAFE - table is completely unused
- ✅ No foreign key constraints block anything
- ✅ No application errors from orphaned table

**Should You Drop the Table?**
- **NO** - Leave it. It's harmless and might be useful for analysis.
- If you really want to remove it:

```sql
-- ONLY if you want to clean up (optional)
DROP TABLE api_key_model_switches;

-- Or use Alembic downgrade
cd /path/to/app
alembic downgrade -1
```

---

## Database Migration Strategy

### Forward Migration (Deployment)
```bash
# CICD runs this automatically
alembic upgrade head

# Creates table: api_key_model_switches
# Adds 2 indexes
# Takes ~1-2 seconds
```

### What if Migration Fails?
```bash
# CICD deployment will FAIL (good - prevents bad deploy)
# Service continues running OLD code
# Database unchanged
# Fix migration and retry
```

### Rollback Migration (Manual - If Needed)
```bash
# SSH to ECS task or run from bastion
cd /app

# Downgrade one version (removes table)
alembic downgrade -1

# Or specific version
alembic downgrade 2025_12_02_1400

# Verify
psql $DATABASE_URL -c "\dt api_key_model_switches"
# Should show: relation does not exist
```

**⚠️ IMPORTANT**: Downgrading drops the table and all switch records.


## Clean Backout Plan (Production Ready)

### Emergency: Disable Immediately (5 minutes)
# ✅ Done - service back to normal
# ⏱️ Total time: ~5 minutes

### Full Rollback: Revert Code (30 minutes)
```bash
# 1. Revert application code
cd /Volumes/moon/repo/mor/Morpheus-Marketplace-API
git checkout main
git revert <commit-hash>
git push origin main

# 2. Wait for CICD pipeline to complete (~10-15 minutes)
# GitHub Actions will automatically:
#   - Build old Docker image
#   - Deploy to ECS
#   - Run health checks

# 3. Verify old code running
aws logs tail /aws/ecs/services/prd/morpheus-api --follow
# Should NOT see any "rate_limit" log entries

# 5. (Optional) Drop table if desired - NOT RECOMMENDED
psql $DB_URL -c "DROP TABLE api_key_model_switches;"
# Or: alembic downgrade -1
```

**Timeline**: ~30 minutes total
**Risk**: Low - old code is proven stable

---

## Database Cleanup Strategy

### Option A: Leave Table (RECOMMENDED)
```
✅ Zero risk
✅ No manual intervention
✅ Can re-enable feature later
✅ Historical data preserved
✅ Old code doesn't care
```

### Option B: Drop Table Later (Optional)
```bash
# Only after confirming backout is permanent

# Method 1: SQL
psql $DB_URL -c "DROP TABLE IF EXISTS api_key_model_switches CASCADE;"

# Method 2: Alembic
alembic downgrade -1

# Verify
psql $DB_URL -c "\dt api_key_model_switches"
# Should show: relation does not exist
```

**When to drop**: Only if you're 100% sure you won't re-enable rate limiting.

---


## Final Checklist Before Deployment

- [ ] Terraform changes reviewed
- [ ] Application code reviewed
- [ ] Task definition includes new env vars ✅ (fixed)
- [ ] Both dev and prd have variables ✅ (fixed)
- [ ] Rate limiting disabled by default ✅
- [ ] Migration file present ✅
- [ ] Backout plan understood ✅
- [ ] Monitoring plan ready ✅
- [ ] Team aware of deployment ✅

**Ready for deployment via CICD pipeline! 🚀**
