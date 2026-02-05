# 🔥 HOTFIX: Model Switch Rate Limiting - Executive Summary

## The Problem We're Solving

```
┌─────────────────────────────────────────────────────────────┐
│  ONE USER (emailuser) IS:                          │
│  ─────────────────────────────────────────────────────────  │
│  • Creating 97.9% of ALL platform sessions                   │
│  • Switching models 3,601 times per day (99.5% rate)        │
│  • Generating blockchain txs every 10-40 seconds            │
│  • Running 24/7 automated testing                           │
│  • Costing thousands in wasted gas fees                     │
└─────────────────────────────────────────────────────────────┘
```

---

## The Solution

**Toggleable rate limiting** at the session service layer that:
- ✅ Tracks model switches per API key in database
- ✅ Enforces hourly (10) and daily (50) limits
- ✅ **Disabled by default** (zero risk)
- ✅ Can be enabled with single env var change
- ✅ Exempts users by email or ID
- ✅ Returns HTTP 429 with retry guidance

---

## What Changed

### Application Code (5 files)
```
Morpheus-Marketplace-API/
├── alembic/versions/
│   └── 2026_02_05_2100_add_model_switch_rate_limiting.py  [NEW]
├── src/
│   ├── services/
│   │   ├── model_switch_rate_limiter.py  [NEW]
│   │   └── session_service.py            [MODIFIED]
│   ├── core/
│   │   └── config.py                     [MODIFIED]
│   └── api/v1/session/
│       └── index.py                      [MODIFIED]
└── [3 documentation files]                [NEW]
```

### Infrastructure (3 files - 04-prd ONLY)
```
Morpheus-Infra/environments/03-morpheus_api/
├── .terragrunt/
│   ├── 00_variables.tf   [MODIFIED - added 6 variables]
│   └── 01_secrets.tf     [MODIFIED - added to secrets]
└── 04-prd/
    └── secret.auto.tfvars [MODIFIED - added config values]
```

**✅ Terraform validates successfully**

---

## Current Configuration (Safe Defaults)

```hcl
# In 04-prd/secret.auto.tfvars

env_model_switch_rate_limit_enabled        = "false"  # 🔴 DISABLED
env_model_switch_max_per_hour              = "10"
env_model_switch_max_per_day               = "50"
env_model_switch_window_seconds            = "300"
env_model_switch_rate_limit_exempt_emails  = ""
env_model_switch_rate_limit_exempt_user_ids = ""
```

---

## Deployment Path

```
┌─────────────────────────────────────────────────────────────┐
│  PHASE 1: Deploy (Disabled State)                           │
└─────────────────────────────────────────────────────────────┘
  1. Run alembic migration (creates table)
  2. Deploy code to production
  3. Apply Terraform (04-prd only)
  4. Restart ECS service
  5. Monitor for 24 hours
  
  Expected: Zero behavior changes, service runs normally
  
┌─────────────────────────────────────────────────────────────┐
│  PHASE 2: Enable (After 24h)                                │
└─────────────────────────────────────────────────────────────┘
  1. Change: env_model_switch_rate_limit_enabled = "true"
  2. Apply Terraform
  3. Restart ECS service
  4. Monitor closely
  
  Expected: Milan gets rate limited, others unaffected
```

---

## Impact Projection

### Before Enabling
| Metric | Value |
|--------|-------|
| Milan's sessions/day | 3,618 |
| Platform total/day | 3,695 |
| Milan's % of load | 97.9% |
| Blockchain waste | ~3,600 txs/day |

### After Enabling (Expected)
| Metric | Value |
|--------|-------|
| Milan's sessions/day | **50** (capped) |
| Platform total/day | **127** |
| Milan's % of load | **39%** |
| Blockchain waste | **98.6% reduction** |
| HTTP 429 responses | ~3,550/day (for Milan) |

---

## How to Use Exemptions

### Exempt Milan (by User ID)
```hcl
env_model_switch_rate_limit_exempt_user_ids = "3"
```

### Exempt Milan (by Email)
```hcl
env_model_switch_rate_limit_exempt_emails = "emailuser"
```

### Exempt Multiple Users
```hcl
env_model_switch_rate_limit_exempt_emails = "emailuser,admin@mor.org"
env_model_switch_rate_limit_exempt_user_ids = "3,5,10"
```

---

## Emergency Controls

### Disable Instantly
```bash
# 1. Edit tfvars
env_model_switch_rate_limit_enabled = "false"

# 2. Apply
cd /path/to/04-prd && terragrunt apply

# 3. Restart
aws ecs update-service --force-new-deployment ...
```

### Monitor Status
```bash
# Check if enabled
aws secretsmanager get-secret-value \
  --secret-id prd-morpheus-api \
  --region us-east-2 | jq '.SecretString | fromjson | .model_switch_rate_limit_enabled'

# Check logs
aws logs tail /aws/ecs/services/prd/morpheus-api \
  --filter-pattern "rate_limit" \
  --follow
```

---

## Test Queries

### See Rate Limiting in Action (After Enabling)
```sql
-- Real-time switch activity
SELECT 
  u.email,
  ak.key_prefix,
  to_model,
  switched_at
FROM api_key_model_switches ams
JOIN users u ON ams.user_id = u.id
JOIN api_keys ak ON ams.api_key_id = ak.id
WHERE switched_at >= NOW() - INTERVAL '1 hour'
ORDER BY switched_at DESC;

-- Users hitting limits
SELECT 
  u.email,
  u.id,
  COUNT(*) as switches_last_hour
FROM api_key_model_switches ams
JOIN users u ON ams.user_id = u.id
WHERE switched_at >= NOW() - INTERVAL '1 hour'
GROUP BY u.email, u.id
HAVING COUNT(*) >= 10
ORDER BY switches_last_hour DESC;
```

---

## Key Features

### ✅ Zero-Risk Deployment
- Disabled by default
- No behavior changes until enabled
- Simple toggle to enable/disable

### ✅ Flexible Exemptions
- By user email: `emailuser`
- By user ID: `3`
- Mix and match as needed

### ✅ Comprehensive Logging
```
event_type: "rate_limit_check_passed"     # Normal operation
event_type: "rate_limit_exceeded_hourly"  # Limit hit
event_type: "rate_limit_exempted_email"   # User bypassed
event_type: "model_switch_recorded"       # Switch tracked
```

### ✅ Clear Error Response
```json
HTTP 429 Too Many Requests
Retry-After: 3245

{
  "error": "rate_limit_exceeded",
  "limit_type": "hourly",
  "limit": 10,
  "current": 11,
  "retry_after_seconds": 3245,
  "user": "emailuser"
}
```

---

## Files Ready for Review

All changes made, **NOT committed** as requested:

```bash
# API Repository
cd /Volumes/moon/repo/mor/Morpheus-Marketplace-API
git status

# Infrastructure Repository  
cd /Volumes/moon/repo/mor/Morpheus-Infra
git status
```

---

## Next Actions

1. **Review** this implementation
2. **Commit** when satisfied
3. **Deploy** to production (see DEPLOYMENT_CHECKLIST.md)
4. **Monitor** for 24 hours with rate limiting disabled
5. **Enable** when confident
6. **Observe** Milan's sessions drop from 3,600/day to 50/day

---

## Confidence Level: ✅ HIGH

- ✅ Terraform validates successfully
- ✅ Python syntax validated (no errors)
- ✅ No linter errors
- ✅ Safe defaults configured (disabled)
- ✅ Rollback plan documented
- ✅ 04-prd only (dev untouched)
- ✅ Comprehensive logging
- ✅ Clear documentation

**This hotfix is production-ready.**
