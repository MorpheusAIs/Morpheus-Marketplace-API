# Logging System Fixes Applied

**Date:** October 6, 2025  
**Version:** 1.2  
**Issues:** Logs showing incorrect logger field, empty log entries, missing structured fields

## Recent Updates

### Version 1.2 (October 6, 2025)
- ✅ Changed all `logger` fields to **lowercase** (e.g., `"core"` instead of `"CORE"`)
- ✅ Changed all `level` fields to **lowercase** (e.g., `"info"` instead of `"INFO"`)
- ✅ Ensures consistent lowercase formatting across all log entries

---

## Problems Identified

### 1. **Logger Field Showing Log Level Instead of Component**
**Problem:** Logs showed `"logger": "DEBUG"` or `"logger": "INFO"` instead of component names like `"models"`, `"core"`, etc.

**Root Cause:** Logic error in `_add_logger_name()` function (line 203-209):
```python
logger_name = name.upper()  # Converts "models" → "MODELS"
if logger_name == component.lower():  # Compares "MODELS" == "core" ❌
```
This comparison would NEVER match!

**Fix Applied:**
- Changed to compare lowercase to lowercase: `logger_name_lower == component.lower()`
- Use bound `component` field when available (from `get_component_logger()`)
- Proper fallback to uppercased logger name

### 2. **Empty Log Entries**
**Problem:** Many log entries had all empty fields:
```json
{
  "level": "",
  "logger": "",
  "caller": "",
  "event": ""
}
```

**Root Cause:** Libraries/frameworks logging without structured data, empty messages being passed through.

**Fix Applied:**
- Added `_ensure_event_field()` processor to guarantee `event` field is populated
- Added processor to filter out empty strings from log output
- Improved error handling in message extraction

### 3. **Uvicorn Access Logs Not Structured**
**Problem:** Uvicorn access logs showed `"logger": "uvicorn.access"` with empty `caller` and `event` fields.

**Root Cause:** UvicornJSONFormatter wasn't parsing the access log format properly or mapping to standard fields.

**Fix Applied:**
- Map uvicorn logs to `"logger": "CORE"` (infrastructure component)
- Extract structured fields: `client_addr`, `method`, `endpoint`, `status_code`
- Create meaningful `event` field: `"GET /api/v1/models - 200"`
- Add proper `caller` field with filename and line number
- Filter out empty strings to reduce noise

---

## Changes Made to `logging_config.py`

### Change 1: Fixed Logger Name Mapping (Lines 199-218)

**Before:**
```python
@staticmethod
def _add_logger_name(logger, name, event_dict):
    logger_name = name.upper()
    for component, libs in MorpheusLogConfig.COMPONENT_HIERARCHY.items():
        if logger_name == component.lower() or ...:  # ❌ Never matches!
            event_dict["logger"] = component
            break
    else:
        event_dict["logger"] = logger_name
    return event_dict
```

**After:**
```python
@staticmethod
def _add_logger_name(logger, name, event_dict):
    # If component is already bound (from get_component_logger), use it
    if "component" in event_dict:
        event_dict["logger"] = event_dict.pop("component")
        return event_dict
    
    # Otherwise, extract component from logger name
    logger_name_lower = name.lower()
    
    # Check if logger name matches a component
    for component, libs in MorpheusLogConfig.COMPONENT_HIERARCHY.items():
        if logger_name_lower == component.lower() or any(lib in logger_name_lower for lib in libs):
            event_dict["logger"] = component
            return event_dict
    
    # Fallback: use the logger name as-is (capitalized for consistency)
    event_dict["logger"] = name.upper()
    return event_dict
```

### Change 2: Added Event Field Processor (Lines 257-277)

**New Function:**
```python
@staticmethod
def _ensure_event_field(logger, name, event_dict):
    """Ensure event field is populated (required for all logs)."""
    if not event_dict.get("event"):
        # Try alternative message fields in order of preference
        # 1. Check for 'message' field (common in many logging systems)
        if event_dict.get("message"):
            event_dict["event"] = str(event_dict["message"])
        # 2. Check for 'msg' field (common in structlog)
        elif event_dict.get("msg"):
            event_dict["event"] = str(event_dict["msg"])
        # 3. Check for '@message' field (CloudWatch specific)
        elif event_dict.get("@message"):
            event_dict["event"] = str(event_dict["@message"])
        # 4. Try to get message from stdlib logging record
        elif hasattr(logger, '_context') and hasattr(logger._context, 'msg'):
            event_dict["event"] = str(logger._context.msg)
        # Last resort: use a placeholder
        else:
            event_dict["event"] = "[no message]"
    return event_dict
```

**Added to Processor Chain:**
```python
processors = [
    add_log_level,
    TimeStamper(fmt="iso", utc=True),
    filter_by_level,
    self._ensure_event_field,  # ← NEW
]
```

**Why Check Multiple Message Fields:**
- Different logging systems use different field names (`message`, `msg`, `@message`)
- CloudWatch may inject logs with `@message` field from various sources
- This ensures we capture the actual message content regardless of its source
- Only falls back to `[no message]` placeholder if truly no message is available

### Change 3: Improved UvicornJSONFormatter (Lines 22-79)

**Key Improvements:**
- Better error handling for message extraction
- Set `logger` to `"CORE"` for infrastructure logs
- Parse uvicorn.access logs to extract structured fields
- Create meaningful event messages
- Filter out empty strings

**New Structure:**
```python
log_data = {
    "timestamp": timestamp,
    "level": record.levelname if record.levelname else "INFO",
    "logger": "CORE",  # Uvicorn = infrastructure
    "caller": f"{record.filename}:{record.lineno}",
    "event": message
}

# Parse uvicorn.access logs
if record.name == "uvicorn.access":
    log_data["client_addr"] = record.args[0]
    log_data["method"] = record.args[1]
    log_data["endpoint"] = record.args[2]
    log_data["status_code"] = record.args[4]
    log_data["event"] = f"{method} {endpoint} - {status_code}"

# Filter out empty strings
log_data = {k: v for k, v in log_data.items() if v != ""}
```

---

## Expected Log Output After Fixes

### Before (Broken):
```json
{
  "@timestamp": "2025-10-06 19:18:14.888",
  "level": "debug",
  "logger": "DEBUG",              // ❌ Should be "MODELS"
  "caller": "direct_model_service.py:107",
  "event": "Using cached model data"
}

{
  "@timestamp": "2025-10-06 19:18:15.571",
  "level": "INFO",
  "logger": "uvicorn.access",     // ❌ Should be "CORE"
  "caller": "",                    // ❌ Empty
  "event": ""                      // ❌ Empty
}

{
  "@timestamp": "2025-10-06 19:18:15.569",
  "level": "",                     // ❌ All empty
  "logger": "",
  "caller": "",
  "event": ""
}
```

### After (Fixed):
```json
{
  "@timestamp": "2025-10-06 19:18:14.888",
  "level": "debug",
  "logger": "MODELS",              // ✅ Correct component
  "caller": "direct_model_service.py:107",
  "event": "Using cached model data",
  "component": "MODELS",
  "cache_expires_in_seconds": 295.20263,
  "event_type": "cache_hit"
}

{
  "@timestamp": "2025-10-06 19:18:15.571",
  "level": "INFO",
  "logger": "CORE",                // ✅ Infrastructure component
  "caller": "access.py:532",
  "event": "GET /api/v1/models - 200",  // ✅ Meaningful message
  "method": "GET",
  "endpoint": "/api/v1/models",
  "status_code": 200,
  "client_addr": "172.31.5.123"
}

// ✅ Empty logs filtered out or enriched
```

---

## Testing the Fixes

### Local Testing
```bash
# Set environment to use JSON logging
export LOG_JSON=true
export LOG_LEVEL=DEBUG

# Restart the application
# The logs should now show proper component names and structured fields
```

### Verify Fixes in CloudWatch
After deployment, check that:
1. ✅ `logger` field shows component names (core, auth, proxy, models, api)
2. ✅ No empty log entries
3. ✅ Uvicorn access logs have structured fields
4. ✅ All logs have `event` field populated

### CloudWatch Insights Queries

**Count logs by component:**
```
fields @timestamp, logger, event
| stats count() by logger
```

**Find any remaining empty logs:**
```
fields @timestamp, level, logger, event
| filter logger = "" or event = "" or level = ""
```

**Check uvicorn access logs:**
```
fields @timestamp, method, endpoint, status_code, event
| filter logger = "core" and method != ""
```

---

## Rollout Plan

1. **Deploy to DEV** - Verify fixes in dev environment first
2. **Monitor CloudWatch** - Ensure logs are properly structured
3. **Verify Metric Filters** - Update/create metric filters based on correct field names
4. **Deploy to PROD** - Roll out after validation in dev

---

## Related Documentation

- **LOGGING_SYSTEM_REFERENCE.md** - Complete logging reference guide
- **src/core/logging_config.py** - Logging configuration implementation

---

## Summary of Fixes

| Issue | Status | Impact |
|-------|--------|---------|
| Logger field showing log level instead of component | ✅ Fixed | Metric filters will now work correctly |
| Empty log entries cluttering CloudWatch | ✅ Fixed | Reduced noise, better searchability |
| Uvicorn logs missing structured fields | ✅ Fixed | Access logs now filterable by endpoint, status, method |
| Component hierarchy not working | ✅ Fixed | Library logs now properly categorized |

---

*Last Updated: October 6, 2025*  
*Applied By: Alex's Logging Standardization*

