# Direct Model Service Verification Guide

This guide provides comprehensive methods to verify that the Direct Model Fetching Service is working correctly in your ECS deployment.

## 🏥 Health Checks

### 1. Basic Health Check

Check the main health endpoint which includes model service status:

```bash
curl https://api.dev.mor.org/health
```

**Expected Response:**
```json
{
  "status": "ok",
  "model_service": {
    "status": "healthy",
    "model_count": 25,
    "cache_info": {
      "cached_models": 25,
      "cache_expiry": "2025-01-15T10:35:00.123456",
      "seconds_until_expiry": 245.5,
      "last_hash": "abc123...",
      "cache_duration": 300
    },
    "active_models_url": "https://active.dev.mor.org/active_models.json",
    "default_fallback_model": "mistral-31-24b"
  }
}
```

### 2. Detailed Model Health Check

Check the dedicated model service health endpoint:

```bash
curl https://api.dev.mor.org/health/models
```

**Expected Response:**
```json
{
  "status": "healthy",
  "service_config": {
    "active_models_url": "https://active.dev.mor.org/active_models.json",
    "default_fallback_model": "mistral-31-24b",
    "cache_duration_seconds": 300
  },
  "cache_stats": {
    "cached_models": 25,
    "cache_expiry": "2025-01-15T10:35:00.123456",
    "seconds_until_expiry": 245.5
  },
  "model_counts": {
    "total_models": 25,
    "active_mappings": 25,
    "blockchain_ids": 25
  },
  "test_results": {
    "venice-uncensored": {
      "status": "resolved",
      "blockchain_id": "0xb603e5c973fae19c86079a068abdc5c119c276e6117fb418a2248f6efd95612d"
    },
    "mistral-31-24b": {
      "status": "resolved", 
      "blockchain_id": "0x..."
    }
  },
  "available_models": ["venice-uncensored", "mistral-31-24b", "..."]
}
```

## 🧪 External Verification Script

Use the provided verification script to test all endpoints:

```bash
# Basic verification
python scripts/verify_model_service.py --url https://api.dev.mor.org

# Verbose verification with output file
python scripts/verify_model_service.py --url https://api.dev.mor.org --verbose --output verification_results.json
```

**What it tests:**
- Main health endpoint model service integration
- Dedicated model health endpoint
- Models list endpoint (/api/v1/models)
- Model resolution via chat completions (auth errors expected)

## 📊 ECS Log Analysis

Analyze ECS CloudWatch logs to verify service operation:

```bash
# Install boto3 if not already installed
pip install boto3

# Analyze logs from the last hour
python scripts/analyze_ecs_logs.py --log-group /aws/ecs/morpheus-api-dev --hours 1

# Analyze logs for specific time period
python scripts/analyze_ecs_logs.py --log-group /aws/ecs/morpheus-api-dev --start "2025-01-15 10:00" --end "2025-01-15 11:00"

# Focus on model-related logs only
python scripts/analyze_ecs_logs.py --log-group /aws/ecs/morpheus-api-dev --hours 2 --filter-models --verbose
```

### Key Log Patterns to Look For

**✅ Healthy Patterns:**
```
DirectModelService initialized with 300s cache duration
Fetching models from https://active.dev.mor.org/active_models.json
✅ Successfully refreshed 25 models
Direct model service initialized with 25 models
[MODEL_DEBUG] Found mapping: venice-uncensored -> 0xb603e5c973fae19c86079a068abdc5c119c276e6117fb418a2248f6efd95612d
```

**⚡ Cache Efficiency Patterns:**
```
Models data unchanged (304 Not Modified), extending cache
Models data unchanged (same hash), extending cache
Cache extended for 300 seconds
```

**❌ Error Patterns to Investigate:**
```
Error fetching models: [error details]
Error resolving model 'model-name': [error details]
Failed to initialize direct model service: [error details]
```

## 🔍 Manual Model Resolution Testing

### Test Model Resolution via Models Endpoint

```bash
# Get list of available models
curl https://api.dev.mor.org/api/v1/models

# Should return models from DirectModelService with ModelType field
{
  "object": "list",
  "data": [
    {
      "id": "venice-uncensored",
      "blockchainID": "0xb603e5c973fae19c86079a068abdc5c119c276e6117fb418a2248f6efd95612d",
      "created": 1234567890,
      "tags": ["uncensored", "chat"],
      "modelType": "UNKNOWN"
    },
    {
      "id": "text-embedding-bge-m3",
      "blockchainID": "0x34cd811e3e4710103080f363bb698a933a4cf13c5ab834e2c7652cfdd537bd96",
      "created": 1753982834,
      "tags": ["Embeddings", "Titan", "LMR"],
      "modelType": "EMBEDDING"
    },
    {
      "id": "Whisper-1",
      "blockchainID": "0xcb7f0bcc6a8997d0163e3fcac4eca181ba0a4037caa1eb89bf437c66858c9826",
      "created": 1748544095,
      "tags": ["btbf", "transcribe", "s2t", "speech"],
      "modelType": "STT"
    }
  ]
}
```

#### Model Types

The API now includes a `modelType` field for each model:

- **`LLM`**: Large Language Models (chat, text generation)
- **`EMBEDDING`**: Text embedding models
- **`TTS`**: Text-to-Speech models  
- **`STT`**: Speech-to-Text models
- **`UNKNOWN`**: Models without a specific type classification

### Test Model Resolution via Chat Completions

```bash
# Test venice-uncensored resolution (will fail with auth error, but model should resolve)
curl -X POST https://api.dev.mor.org/api/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "venice-uncensored",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 1
  }'

# Expected: 401/403 auth error (model resolved successfully)
# Not expected: 400/422 model resolution error
```

## 📈 Monitoring Key Metrics

### 1. Cache Performance

Monitor cache hit ratio from `/health/models`:
- **Good**: `seconds_until_expiry` > 0 (cache is fresh)
- **Good**: Logs show "304 Not Modified" or "same hash" frequently
- **Bad**: Frequent "Successfully refreshed" without cache hits

### 2. Model Resolution Success

Monitor model resolution from logs:
- **Good**: Regular "[MODEL_DEBUG] Found mapping" entries
- **Bad**: Frequent "[MODEL_DEBUG] Using default model" for valid models

### 3. Service Availability

Monitor service health:
- **Good**: `/health` returns `model_service.status: "healthy"`
- **Good**: `/health/models` returns `status: "healthy"`
- **Bad**: Any `"unhealthy"` or `"error"` status

### 4. Response Times

Monitor API response times:
- **Good**: `/health/models` < 1000ms (cache hit)
- **Acceptable**: `/health/models` < 2000ms (cache miss + CloudFront fetch)
- **Bad**: `/health/models` > 5000ms (network issues)

## 🚨 Alerting Recommendations

### CloudWatch Alarms

1. **Model Service Health**
   ```
   Metric: Custom metric from health check
   Condition: model_service.status != "healthy"
   Duration: 2 consecutive periods
   ```

2. **Model Count**
   ```
   Metric: model_service.model_count
   Condition: < 5 models
   Duration: 1 period
   ```

3. **API Response Time**
   ```
   Metric: /health/models response time
   Condition: > 5000ms
   Duration: 3 consecutive periods
   ```

### Log-Based Alerts

1. **Model Fetch Errors**
   ```
   Filter: "Error fetching models"
   Threshold: > 5 occurrences in 5 minutes
   ```

2. **Model Resolution Failures**
   ```
   Filter: "[MODEL_DEBUG] Using default model" AND NOT "No model specified"
   Threshold: > 10 occurrences in 5 minutes
   ```

## 🔧 Troubleshooting

### Issue: No Models Available
**Symptoms:** `model_count: 0` in health checks
**Checks:**
1. Verify `ACTIVE_MODELS_URL` environment variable
2. Check network connectivity to active.dev.mor.org
3. Verify CloudFront distribution is healthy
4. Check ECS task has internet access

### Issue: Model Resolution Failing
**Symptoms:** Models falling back to default frequently
**Checks:**
1. Verify specific model exists in `/api/v1/models` response
2. Check model name spelling and case sensitivity
3. Verify model is not marked as `IsDeleted: true`

### Issue: High Response Times
**Symptoms:** `/health/models` taking > 2 seconds consistently
**Checks:**
1. Check cache hit ratio - should be >90%
2. Verify ECS task memory usage
3. Check network latency to CloudFront
4. Consider reducing cache duration if memory constrained

### Issue: Cache Not Working
**Symptoms:** Every request fetches from CloudFront
**Checks:**
1. Verify ECS task memory allocation
2. Check for memory pressure in container
3. Verify cache duration setting
4. Check for rapid container restarts

## 📋 Deployment Checklist

Before deploying to production:

- [ ] Health endpoints return healthy status
- [ ] External verification script passes all tests
- [ ] ECS log analysis shows no errors
- [ ] venice-uncensored model resolves correctly
- [ ] Cache hit ratio > 90% after warmup
- [ ] Response times < 1000ms for cached requests
- [ ] CloudWatch alarms configured
- [ ] Log-based alerts configured
- [ ] Runbook updated with troubleshooting steps

## 🔄 Environment-Specific URLs

### Development
- Health: `https://api.dev.mor.org/health`
- Model Health: `https://api.dev.mor.org/health/models`
- Models List: `https://api.dev.mor.org/api/v1/models`
- Active Models Source: `https://active.dev.mor.org/active_models.json`

### Production
- Health: `https://api.mor.org/health`
- Model Health: `https://api.mor.org/health/models`
- Models List: `https://api.mor.org/api/v1/models`
- Active Models Source: `https://active.mor.org/active_models.json`

## 📞 Support

If verification fails or issues are detected:

1. Run the external verification script with `--verbose`
2. Analyze recent ECS logs with the log analysis script
3. Check CloudWatch metrics and alarms
4. Review the troubleshooting section above
5. Check the ECS task health and resource utilization

The Direct Model Service is designed to be resilient and self-healing. Most issues resolve automatically within 5 minutes (one cache cycle).
