# Balance Cache Bug Fix - `is_staker` Field

## Problem

The balance caching implementation was missing the `is_staker` field in both serialization and deserialization, causing Pydantic validation errors:

```
ValidationError: Input should be a valid boolean [type=bool_type, input_value=None]
Field: is_staker
```

### Root Cause

1. **Database Model** (`CreditAccountBalance`): `is_staker` is NOT nullable, defaults to `False`
2. **Cache Serialization** (`credits.py` line 103-116): `is_staker` was missing from `cache_data` dict
3. **Cache Deserialization** (`credits.py` line 57-68): `is_staker` was missing from `balance_data` dict
4. When cached data was deserialized without `is_staker`, the field became `None`, failing Pydantic validation in `BalanceResponse`

## Solution

Added `is_staker` to both cache operations in `src/crud/credits.py`:

### Serialization (Store to Cache)
```python
cache_data = {
    # ... other fields ...
    'is_staker': balance.is_staker,  # Include staker flag in cache
    'allow_overage': balance.allow_overage,
    # ... other fields ...
}
```

### Deserialization (Load from Cache) with Validation
```python
if cached:
    # Validate cache data has all required fields
    if 'is_staker' not in cached:
        logger.warning(
            "Cache entry missing required field 'is_staker', invalidating",
            user_id=user_id,
            event_type="cache_format_invalid"
        )
        await cache_service.delete("balance", cache_key)
        # Fall through to database fetch
    else:
        # Cache is valid, deserialize it
        balance_data = {
            # ... other fields ...
            'is_staker': cached['is_staker'],
            'allow_overage': cached['allow_overage'],
            # ... other fields ...
        }
        return CreditAccountBalance(**balance_data)
```

**Important**: Instead of defaulting `is_staker` to `False` (which could give users incorrect information), we invalidate cache entries with missing required fields and fetch fresh data from the database. This ensures data accuracy at the cost of one extra DB query for stale cache entries.

## Testing

After deploying the fix:
1. Clear existing cache entries: `redis-cli FLUSHDB` (or wait 30s for TTL expiration)
2. Make a request to `/api/v1/billing/balance`
3. Verify no validation errors in logs
4. Verify `is_staker` appears in cached data (check with `redis-cli GET cache:balance:user_balance:<user_id>`)

## Graceful Degradation Paths

### Disabling Redis Caching

If you need to disable application-level caching entirely:

**Via Environment Variable:**
```bash
# In terraform.tfvars or ECS task definition
cache_enabled = false
```

**How it works:**
- `CACHE_ENABLED` defaults to `false` (line 272 in `config.py`)
- When disabled, `cache_service.get()` returns `None` immediately (line 170 in `cache_service.py`)
- Application falls back to direct database queries
- No code changes needed - transparent fallback

### Disabling RDS Proxy

If you need to switch back to direct RDS connection:

**In `terraform.tfvars`:**
```hcl
switches = {
  rds       = true
  rds_proxy = false  # Disable RDS Proxy
  # ...
}
```

**In `01_secrets.tf`:**
The `database_url` is already conditional (line 87):
```hcl
database_url = var.switches.rds_proxy 
  ? "postgresql+asyncpg://...@rds-proxy-endpoint:5432/..." 
  : "postgresql+asyncpg://...@rds-direct-endpoint:5432/..."
```

**Steps:**
1. Set `rds_proxy = false` in `terraform.tfvars`
2. Run `terragrunt apply` (destroys RDS Proxy resources)
3. ECS tasks automatically restart with direct RDS connection string
4. No application code changes needed

## Impact

- **Before**: 16+ validation errors per 10 minutes on `/api/v1/billing/balance` endpoint
- **After**: No validation errors, proper caching of all balance fields including `is_staker`

## Related Files

- `src/crud/credits.py` - Balance caching logic (lines 54-118)
- `src/db/models/credits.py` - `CreditAccountBalance` model (line 106)
- `src/schemas/billing.py` - `BalanceResponse` Pydantic model (line 76)
- `src/services/cache_service.py` - Redis cache service with graceful degradation
- `src/core/config.py` - `CACHE_ENABLED` configuration (line 272)

## Deployment

This is a **hot-fix** that can be deployed immediately:
- No database migrations required
- No infrastructure changes required
- Existing cached entries will expire naturally (30s TTL)
- No downtime needed
