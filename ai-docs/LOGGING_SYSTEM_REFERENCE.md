# Morpheus API Logging System Reference

## Overview

The Morpheus API uses **structlog** for structured JSON logging with component-based log level control. This document provides a complete reference for building CloudWatch metric filters and ensuring consistency.

> **Note:** Logging system bugs were fixed on October 6, 2025. See [LOGGING_FIXES_APPLIED.md](./LOGGING_FIXES_APPLIED.md) for details on the fixes.

---

## 1. Logger Components

### Component Types
The system has **5 primary components**, each with its own logger:

| Component | Function | Usage |
|-----------|----------|-------|
| **CORE** | `get_core_logger()` | Infrastructure, middleware, uvicorn |
| **AUTH** | `get_auth_logger()` | Authentication, users, API keys, Cognito |
| **PROXY** | `get_proxy_logger()` | Proxy router service communication |
| **MODELS** | `get_models_logger()` | Model service, routing, mapping |
| **API** | `get_api_logger()` | API endpoints (chat, session, embeddings) |

### Files by Component

**CORE:**
- `src/core/cors_middleware.py`
- `src/core/key_vault.py`
- `src/core/local_testing.py`
- `src/main.py`
- All uvicorn/infrastructure logs

**AUTH:**
- `src/core/encryption.py`
- `src/api/v1/auth/index.py`
- `src/services/cognito_service.py`
- `src/crud/user.py`
- `src/crud/private_key.py`
- `src/dependencies.py`

**PROXY:**
- `src/services/proxy_router_service.py`

**MODELS:**
- `src/core/direct_model_service.py`
- `src/core/model_routing.py`
- `src/core/model_types.py`
- `src/services/model_mapper.py`

**API:**
- `src/api/v1/session/index.py`
- `src/api/v1/models/index.py`
- `src/api/v1/embeddings/index.py`
- `src/api/v1/chat/index.py`
- `src/services/session_service.py`

---

## 2. Standard Log Structure

### Automatic Fields (Always Present)

Every log entry automatically includes these fields:

```json
{
  "timestamp": "2025-10-06T10:15:30.123456Z",
  "level": "info",
  "logger": "MODELS",
  "caller": "direct_model_service.py:107",
  "event": "Using cached model data"
}
```

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `timestamp` | ISO 8601 string | UTC timestamp | `"2025-10-06T10:15:30.123456Z"` |
| `level` | string | Log level (lowercase) | `"debug"`, `"info"`, `"warning"`, `"error"`, `"critical"` |
| `logger` | string | Component name (uppercase) | `"CORE"`, `"AUTH"`, `"PROXY"`, `"MODELS"`, `"API"` |
| `caller` | string | Source file and line | `"direct_model_service.py:107"` |
| `event` | string | Main message | `"Using cached model data"` |

---

## 3. Standard Contextual Fields

### Common Fields (Used Across Components)

These fields should be used **consistently** when applicable:

#### **Identity & Session Fields**
| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `user_id` | integer | User database ID | `123` |
| `api_key_id` | integer | API key database ID | `456` |
| `session_id` | string | Blockchain session ID | `"0x1234..."` |
| `request_id` | string | Unique request trace ID | `"a7f3c9d2"` |
| `cognito_user_id` | string | Cognito user ID | `"us-east-2_abc123"` |

#### **Model & Blockchain Fields**
| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `model` | string | Model name | `"mistral-31-24b"` |
| `target_model` | string | Target blockchain ID | `"0x8e5c..."` |
| `blockchain_id` | string | Blockchain identifier | `"0x8e5c..."` |
| `requested_model` | string | Model requested by client | `"gpt-4"` |

#### **Error & Status Fields**
| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `error` | string | Error message | `"Connection timeout"` |
| `error_type` | string | Error category | `"authentication_error"`, `"network_error"` |
| `status_code` | integer | HTTP status code | `200`, `401`, `500` |
| `event_type` | string | Event classification | `"session_creation_success"` |

#### **Operational Fields**
| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `endpoint` | string | API endpoint path | `"/api/v1/chat/completions"` |
| `method` | string | HTTP method | `"POST"`, `"GET"` |
| `duration` | float | Operation duration (seconds) | `1.234` |
| `using_fallback` | boolean | Using fallback key | `true`, `false` |
| `client_addr` | string | Client IP address | `"172.31.5.123"` |

---

## 4. Event Type Taxonomy

### Naming Convention
Event types follow this pattern: `<object>_<action>_<result>`

**Examples:**
- `session_creation_start`
- `session_creation_success`
- `session_creation_error`
- `private_key_lookup`
- `model_routing_fallback`
- `cache_hit`
- `cache_miss`

### Common Event Types by Component

**AUTH Component:**
```
user_registration_start
user_registration_success
api_key_creation_success
private_key_added
private_key_error
authentication_error
cognito_user_fetch_success
```

**PROXY Component:**
```
proxy_session_creation_start
proxy_session_response
proxy_request_start
proxy_request_success
proxy_request_error
network_error
timeout_error
```

**MODELS Component:**
```
model_fetch_start
cache_hit
cache_miss
model_resolution_success
model_fallback
model_sync_complete
```

**API Component:**
```
request_details
session_lookup_start
session_lookup_success
stream_generator_start
embeddings_request_start
chat_completion_start
```

**CORE Component:**
```
cors_origin_allowed
cors_preflight_handled
database_connection_success
session_cleanup_start
```

---

## 5. Logging Patterns

### Pattern 1: Simple Info Log
```python
logger.info("Session created successfully",
           session_id=session_id,
           user_id=user.id,
           event_type="session_creation_success")
```

### Pattern 2: Error with Context
```python
logger.error("Failed to fetch model data",
            model_id=model_id,
            error=str(e),
            error_type="network_error",
            event_type="model_fetch_error")
```

### Pattern 3: Debug with Detailed Context
```python
logger.debug("Proxy router request",
            url=url,
            method="POST",
            request_body=json_data,
            event_type="proxy_request_debug")
```

### Pattern 4: Bound Logger (Contextual Logger)
```python
# Create a bound logger with persistent context
request_logger = logger.bind(
    request_id=request_id,
    user_id=user.id,
    endpoint="/api/v1/chat/completions"
)

# All subsequent logs include bound context
request_logger.info("Starting request", event_type="request_start")
request_logger.info("Request completed", duration=1.23, event_type="request_complete")
```

---

## 6. Environment Configuration

### Environment Variables

```bash
# Master log level (applies to all components unless overridden)
LOG_LEVEL=INFO                    # DEBUG, INFO, WARNING, ERROR, CRITICAL

# Output format
LOG_JSON=true                     # true (JSON) or false (console)
LOG_IS_PROD=true                  # true (production) or false (development)

# Component-specific overrides
LOG_LEVEL_CORE=WARN              # Reduce infrastructure noise
LOG_LEVEL_AUTH=INFO              # Keep auth logging
LOG_LEVEL_PROXY=DEBUG            # Verbose proxy debugging
LOG_LEVEL_MODELS=INFO            # Model service logging
LOG_LEVEL_API=INFO               # API endpoint logging
```

### Production Settings
```bash
LOG_LEVEL=INFO
LOG_JSON=true
LOG_IS_PROD=true
LOG_LEVEL_CORE=WARN        # Reduce uvicorn access log noise
LOG_LEVEL_AUTH=WARN
```

### Development Settings
```bash
LOG_LEVEL=DEBUG
LOG_JSON=false
LOG_IS_PROD=false
```

---

## 7. CloudWatch Metric Filter Patterns

### Filter by Component
```
{ $.logger = "AUTH" }
{ $.logger = "PROXY" }
{ $.logger = "API" }
{ $.logger = "MODELS" }
{ $.logger = "CORE" }
```

### Filter by Log Level
```
{ $.level = "error" }
{ $.level = "warning" || $.level = "error" }
```

### Filter by Event Type
```
{ $.event_type = "session_creation_error" }
{ $.event_type = "*_error" }
{ $.event_type = "*_success" }
{ $.event_type = "cache_hit" }
```

### Complex Filters

**All authentication errors:**
```
{ $.logger = "AUTH" && $.level = "error" }
```

**Proxy timeouts:**
```
{ $.logger = "PROXY" && $.error_type = "timeout_error" }
```

**Failed session creations:**
```
{ $.event_type = "session_creation_error" }
```

**Slow requests (>2 seconds):**
```
{ $.duration > 2 }
```

**Fallback key usage:**
```
{ $.using_fallback = true }
```

**API access by endpoint:**
```
{ $.logger = "CORE" && $.endpoint = "/api/v1/chat/completions" }
```

---

## 8. Field Usage Guidelines

### ✅ DO: Use Consistent Field Names

**Good:**
```python
logger.info("User authenticated",
           user_id=user.id,
           api_key_id=api_key.id,
           event_type="authentication_success")
```

**Bad:**
```python
logger.info("User authenticated",
           uid=user.id,              # ❌ Should be user_id
           keyID=api_key.id,         # ❌ Should be api_key_id
           type="auth_success")      # ❌ Should be event_type
```

### ✅ DO: Include event_type for Classification

Always include `event_type` for metric filtering:
```python
logger.info("Operation completed",
           event_type="operation_completed")  # ✅
```

### ✅ DO: Use Structured Data, Not String Formatting

**Good:**
```python
logger.error("Database connection failed",
            host=db_host,
            port=db_port,
            error=str(e),
            event_type="db_connection_error")
```

**Bad:**
```python
logger.error(f"Database connection failed to {db_host}:{db_port}: {str(e)}")  # ❌
```

### ✅ DO: Use Bound Loggers for Request Context

```python
# Bind context once
request_logger = logger.bind(
    request_id=request_id,
    session_id=session_id
)

# All subsequent logs inherit context
request_logger.info("Starting processing")
request_logger.info("Completed processing")
```

### ❌ DON'T: Mix Field Naming Conventions

**Always use snake_case:**
- ✅ `user_id`, `api_key_id`, `session_id`
- ❌ `userId`, `apiKeyId`, `sessionId`
- ❌ `uid`, `keyId`, `sid`

---

## 9. Metric Filter Examples for CloudWatch

### Dashboard Metrics

**Error Rate by Component:**
```
METRIC_FILTER_NAME: error_rate_by_component
PATTERN: { $.level = "error" }
METRIC_NAME: ErrorCount
DIMENSIONS: logger=$$.logger
```

**Session Creation Success Rate:**
```
METRIC_FILTER_NAME: session_creation_success
PATTERN: { $.event_type = "session_creation_success" }
METRIC_NAME: SessionCreationSuccess
```

**Session Creation Failure Rate:**
```
METRIC_FILTER_NAME: session_creation_error
PATTERN: { $.event_type = "session_creation_error" }
METRIC_NAME: SessionCreationError
```

**Proxy Request Duration:**
```
METRIC_FILTER_NAME: proxy_request_duration
PATTERN: { $.logger = "PROXY" && $.duration > 0 }
METRIC_NAME: ProxyRequestDuration
METRIC_VALUE: $.duration
```

**Fallback Key Usage:**
```
METRIC_FILTER_NAME: fallback_key_usage
PATTERN: { $.using_fallback = true }
METRIC_NAME: FallbackKeyUsage
```

**API Endpoint Traffic:**
```
METRIC_FILTER_NAME: api_endpoint_requests
PATTERN: { $.logger = "CORE" && $.status_code > 0 }
METRIC_NAME: APIRequests
DIMENSIONS: endpoint=$$.endpoint, method=$$.method, status_code=$$.status_code
```

---

## 10. Testing Logging

### Local Testing
```bash
# Enable DEBUG logging for specific component
export LOG_LEVEL_PROXY=DEBUG
export LOG_JSON=false  # Console-friendly output

# Run the application
python -m uvicorn src.main:app --reload
```

### Production Log Inspection
```bash
# View logs with jq for pretty JSON
tail -f logs/app.log | jq .

# Filter for errors only
tail -f logs/app.log | jq 'select(.level == "error")'

# Filter by component
tail -f logs/app.log | jq 'select(.logger == "PROXY")'

# Filter by event type
tail -f logs/app.log | jq 'select(.event_type | contains("error"))'

# Show only API requests
tail -f logs/app.log | jq 'select(.logger == "CORE" and .method != null)'
```

---

## 11. Common Issues & Solutions

### Issue: Inconsistent Field Names
**Problem:** Different parts of code use `userId`, `user_id`, `uid`  
**Solution:** Always use snake_case: `user_id`

### Issue: Missing event_type
**Problem:** Can't filter logs by event classification  
**Solution:** Always include `event_type` in structured logs

### Issue: String Interpolation Instead of Structured Fields
**Problem:** `logger.info(f"User {user_id} logged in")`  
**Solution:** `logger.info("User logged in", user_id=user_id, event_type="user_login")`

### Issue: No Request Context in Logs
**Problem:** Can't trace requests through the system  
**Solution:** Use bound loggers with `request_id`:
```python
request_logger = logger.bind(request_id=request_id)
```

---

## 12. Quick Reference

### Standard Log Call Template
```python
logger.{level}(
    "Human-readable message",
    # Identity
    user_id=user_id,
    api_key_id=api_key_id,
    session_id=session_id,
    # Context
    model=model_name,
    endpoint=endpoint_path,
    # Status
    status_code=200,
    duration=1.23,
    # Classification
    event_type="operation_success",
    error=str(e),  # Only for errors
    error_type="error_category"  # Only for errors
)
```

### Common Field Reference
```python
# Always use these exact names for consistency
user_id           # Not: uid, userId, user_ID
api_key_id        # Not: keyId, apiKeyId, key_id
session_id        # Not: sessionId, sid, session_ID
request_id        # Not: requestId, rid, req_id
event_type        # Not: type, eventType, event_category
error             # Not: err, error_message, exception
error_type        # Not: errorType, err_type
status_code       # Not: statusCode, code, httpCode
endpoint          # Not: path, url, route
method            # Not: http_method, verb
```

---

## 13. Implementation Details

### How Component Loggers Work

```python
# Each component logger is created via get_component_logger()
def get_component_logger(component: str) -> structlog.stdlib.BoundLogger:
    logger = get_logger(component.lower())
    return logger.bind(component=component.upper())

# The _add_logger_name processor extracts the component
# Priority order:
# 1. Bound "component" field (from get_component_logger)
# 2. Logger name matches component (e.g., "models" -> "MODELS")
# 3. Logger name matches library in hierarchy (e.g., "uvicorn" -> "CORE")
# 4. Fallback to uppercased logger name
```

### Processor Chain

Logs flow through this processor chain:
```python
1. add_log_level        # Adds "level" field
2. TimeStamper          # Adds "timestamp" field (ISO 8601)
3. filter_by_level      # Filters based on LOG_LEVEL
4. _ensure_event_field  # Ensures "event" is populated
5. _add_logger_name     # Adds "logger" field (component)
6. _add_caller_info     # Adds "caller" field (file:line)
7. JSONRenderer         # Converts to JSON (if LOG_JSON=true)
```

---

## Summary

### Key Principles

1. **Use component-specific loggers** (`get_core_logger()`, `get_api_logger()`, etc.)
2. **Always include `event_type`** for filtering and metrics
3. **Use consistent field names** (snake_case, from standard list)
4. **Structured data over string formatting**
5. **Bound loggers for request context**
6. **Include identity fields** (`user_id`, `session_id`, `request_id`)

### For Metric Filters

All logs include these **guaranteed fields**:
- `timestamp` (ISO 8601)
- `level` (debug/info/warning/error/critical)
- `logger` (CORE/AUTH/PROXY/MODELS/API)
- `caller` (file:line)
- `event` (main message)

Plus **contextual fields** as appropriate from the standard list above.

### Related Documentation

- **[LOGGING_FIXES_APPLIED.md](./LOGGING_FIXES_APPLIED.md)** - Bug fixes applied October 6, 2025
- **[src/core/logging_config.py](../src/core/logging_config.py)** - Logging configuration implementation

---

*Last Updated: October 6, 2025*  
*Version: 1.1*
*Status: ✅ Production Ready*

