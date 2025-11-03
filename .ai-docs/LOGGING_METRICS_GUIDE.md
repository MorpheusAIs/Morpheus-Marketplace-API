# Logging Metrics Guide

## Overview

This document catalogs all trackable events in the structured logging system for monitoring system utilization, performance, and business metrics.

**Version:** 1.1  
**Last Updated:** October 6, 2025

## Changelog

### Version 1.1 (October 6, 2025)
- ✅ Added `chat_completion_success` event for non-streaming completion tracking
- ✅ Added `cache_miss` event for initial cache fetches
- ✅ Fixed event name `stream_complete` → `stream_completed`
- ✅ Validated all 354+ event types against actual codebase
- ✅ Updated CloudWatch filter patterns with correct event names

---

## Core Session Metrics

### Session Lifecycle Events

| Event Type | Description | Key Fields | Use Case |
|------------|-------------|------------|----------|
| `automated_session_creation_start` | User/API key initiating session creation | `user_id`, `api_key_id`, `requested_model`, `session_duration` | Count session creation attempts |
| `proxy_session_creation_start` | Proxy router session request sent | `target_model`, `blockchain_id` | Track proxy interaction starts |
| `proxy_session_response` | Proxy router session created | `session_id`, `response_data` | **Count successful sessions** |
| `session_created` | Session stored in database | `session_id`, `model`, `expires_at` | Confirm DB persistence |
| `active_session_found` | Existing session reused | `session_id`, `session_model` | Track session reuse efficiency |
| `existing_session_reused` | Session reused without model change | `session_id` | Session efficiency metric |
| `session_model_match` | Requested model matches active session | `session_id`, `model_id` | Session match rate |
| `session_model_mismatch` | Model switch requires new session | `session_id`, `current_model`, `requested_model_id` | Track model switches |
| `proxy_session_closed` | Session closed at proxy level | `session_id` | **Count session closures** |
| `session_marked_inactive` | Session marked inactive in DB | `session_id` | Confirm closure |
| `session_not_found_for_close` | Close attempted on non-existent session | `session_id` | Track closure errors |

**Key Metrics:**
- **Total Sessions Created:** Count `proxy_session_response`
- **Sessions Closed:** Count `proxy_session_closed`
- **Session Reuse Rate:** `existing_session_reused` / `automated_session_creation_start`
- **Average Session Duration:** `session_marked_inactive.timestamp` - `session_created.timestamp`

---

## Chat Completion Metrics

### Prompt/Completion Events

| Event Type | Description | Key Fields | Use Case |
|------------|-------------|------------|----------|
| `chat_completion_request_start` | New chat completion received | `user_id`, `model`, `stream_requested`, `request_id` | **Count total prompts** |
| `chat_request_processing` | Request being processed | `user_id`, `api_key_id` | Track processing starts |
| `chat_request_config` | Configuration details logged | `client_stream_requested`, `has_tools` | Track streaming vs non-streaming |
| `stream_generator_start` | Streaming response initiated | `session_id`, `user_id`, `requested_model` | Count streaming requests |
| `stream_request_start` | Streaming proxy request sent | `session_id` | Track stream starts |
| `chat_completions_start` | Non-streaming proxy request sent | `session_id`, `proxy_router_url` | Track non-stream starts |
| `stream_chunk_received` | Stream chunk received from proxy | `session_id`, `chunk_count` | Monitor stream progress |
| `stream_completed` | Streaming response completed | `session_id`, `chunk_count`, `total_chunks` | **Count completed streams** |
| `chat_completion_success` | Non-streaming completion success | `session_id`, `request_id` | **Count completed non-streams** |

**Key Metrics:**
- **Total Prompts:** Count `chat_completion_request_start`
- **Successful Completions:** Count `stream_completed` + `chat_completion_success`
- **Streaming vs Non-Streaming:** Compare counts
- **Average Chunks per Stream:** Average `total_chunks` from `stream_completed`

---

## Advanced Features Metrics

### Tool Calling

| Event Type | Description | Key Fields | Use Case |
|------------|-------------|------------|----------|
| `tools_detected` | Request includes tool definitions | `tool_count`, `request_id` | Track tool usage |
| `tool_choice_detected` | Explicit tool choice specified | `tool_choice`, `request_id` | Track forced tool calls |
| `tool_calling_request_details` | Detailed tool call logging | `has_tools`, `tool_count`, `tool_message_count` | Analyze tool patterns |
| `tool_content_detected` | Tool messages in conversation | `has_tool_messages`, `has_tool_calls` | Track tool interactions |

**Key Metrics:**
- **Tool Usage Rate:** Requests with `tools_detected` / total requests
- **Tool Call Success:** Track completion after tool detection

### Embeddings

| Event Type | Description | Key Fields | Use Case |
|------------|-------------|------------|----------|
| `embeddings_request_start` | Embedding request initiated | `session_id`, `input_length`, `model` | Count embedding requests |
| `embeddings_request_error` | Embedding request failed | `error`, `error_type` | Track embedding errors |

**Key Metrics:**
- **Total Embeddings:** Count `embeddings_request_start`
- **Embedding Error Rate:** `embeddings_request_error` / `embeddings_request_start`

---

## Error & Retry Metrics

### Session Errors

| Event Type | Description | Key Fields | Use Case |
|------------|-------------|------------|----------|
| `stream_session_expired_detected` | Session expired during stream | `session_id` | Track expiration rate |
| `session_retry_start` | Retrying with new session | `new_session_id`, `original_session_id` | **Count retries** |
| `stream_retry_completed` | Retry successful | `new_session_id`, `original_session_id` | Count successful retries |
| `stream_retry_proxy_error` | Retry failed | `error`, `error_type` | Track retry failures |
| `session_close_error` | Error closing session | `session_id`, `error` | Monitor close failures |

### Request Errors

| Event Type | Description | Key Fields | Use Case |
|------------|-------------|------------|----------|
| `chat_completions_error` | Chat completion failed | `error`, `error_type`, `status_code` | Track request failures |
| `stream_generator_error` | Stream generator error | `error`, `chunk_count` | Monitor streaming issues |
| `proxy_request_error` | Proxy communication failed | `error`, `attempt_number` | Track proxy health |
| `stream_proxy_error` | Proxy error during stream | `status_code`, `error_text` | Monitor proxy errors |

**Key Metrics:**
- **Session Retry Rate:** Count `session_retry_start` / total requests
- **Request Error Rate:** Count errors / total requests
- **Proxy Error Rate:** Count proxy errors / total proxy calls

---

## Authentication & Authorization Metrics

| Event Type | Description | Key Fields | Use Case |
|------------|-------------|------------|----------|
| `api_key_validation_failed` | Invalid API key used | `key_prefix` | Track failed auth attempts |
| `api_key_user_error` | Error getting user from key | `error` | Monitor auth system |
| `jwt_validation_error` | JWT validation failed | `error` | Track JWT issues |
| `local_testing_bypass` | Local testing mode used | `user_id` | Track dev usage |

**Key Metrics:**
- **Failed Auth Rate:** Count validation failures / total requests
- **Active API Keys:** Distinct `api_key_id` values

---

## Model & Configuration Metrics

| Event Type | Description | Key Fields | Use Case |
|------------|-------------|------------|----------|
| `model_service_init_success` | Model service initialized | `model_count`, `cache_duration` | Track startup |
| `cache_hit` | Model data served from cache | `cache_expires_in_seconds` | Monitor cache efficiency |
| `cache_miss` | Model data fetched fresh | - | Track cache misses |
| `cache_refresh` | Cache refreshed | `model_count` | Monitor cache updates |

**Key Metrics:**
- **Cache Hit Rate:** `cache_hit` / (`cache_hit` + `cache_miss`)
- **Available Models:** `model_count` from refresh events

---

## Automation Metrics

| Event Type | Description | Key Fields | Use Case |
|------------|-------------|------------|----------|
| `automation_enabled` | Automation active for user | `user_id`, `session_duration` | Track automation usage |
| `automation_disabled` | Automation disabled for user | `user_id` | Track opt-outs |
| `automation_settings_created` | Default settings created | `user_id` | Track new users |

**Key Metrics:**
- **Automation Adoption Rate:** Users with automation enabled / total users
- **Average Auto Session Duration:** Average `session_duration` from automation events

---

## User Activity Metrics

| Event Type | Description | Key Fields | Use Case |
|------------|-------------|------------|----------|
| `user_creation` | New user created from JWT | `cognito_user_id`, `email` | Track signups |
| `api_key_created` | New API key generated | `user_id`, `api_key_id` | Track key creation |
| `api_key_deleted` | API key deleted | `user_id`, `api_key_id` | Track key deletions |
| `private_key_stored` | Private key saved | `user_id` | Track blockchain integration |
| `user_deletion_complete` | User account deleted | `user_id`, `deleted_data` | Track churn |

**Key Metrics:**
- **New Users:** Count `user_creation`
- **Active Users:** Distinct `user_id` with recent activity
- **API Keys per User:** Count keys by `user_id`

---

## CloudWatch Metric Filter Patterns

### Session Metrics

```
# Session Creations
{ $.event_type = "proxy_session_response" }

# Session Closures
{ $.event_type = "proxy_session_closed" }

# Session Retries
{ $.event_type = "session_retry_start" }
```

### Prompt/Completion Metrics

```
# Total Prompts
{ $.event_type = "chat_completion_request_start" }

# Completed Streams
{ $.event_type = "stream_completed" }

# Completed Non-Streams
{ $.event_type = "chat_completion_success" }

# Total Completions (combined)
{ $.event_type = "stream_completed" || $.event_type = "chat_completion_success" }
```

### Error Metrics

```
# All Errors (generic)
{ $.event_type = *error* }

# Session Errors
{ $.event_type = "session_*_error" }

# Proxy Errors
{ $.event_type = "proxy_*_error" }

# Chat Errors
{ $.event_type = "chat_completions_error" || $.event_type = "stream_generator_error" }
```

### User Activity

```
# New Users
{ $.event_type = "user_creation" }

# Active Sessions per User
{ $.event_type = "session_created" && $.user_id = * }
```

---

## Recommended Dashboards

### Operations Dashboard
- **Sessions Created per Hour:** `proxy_session_response` count
- **Sessions Closed per Hour:** `proxy_session_closed` count  
- **Active Sessions:** Created - Closed
- **Session Retry Rate:** `session_retry_start` / total requests

### Business Metrics Dashboard
- **Total Prompts per Day:** `chat_completion_request_start` count
- **Successful Completions:** `stream_completed` + `chat_completion_success`
- **Success Rate:** Completions / Prompts
- **Active Users per Day:** Distinct `user_id`
- **Prompts per User:** Total prompts / active users

### Performance Dashboard
- **Average Request Duration:** Time between `_start` and `_complete` events
- **Cache Hit Rate:** `cache_hit` / (`cache_hit` + `cache_miss`)
- **Cache Miss Rate:** `cache_miss` / total cache lookups
- **Error Rate:** Error events / total requests
- **Retry Rate:** Retries / total requests

### Feature Adoption Dashboard
- **Tool Usage:** `tools_detected` / total requests
- **Streaming vs Non-Streaming:** Compare request counts
- **Automation Usage:** `automation_enabled` users / total users
- **Embedding Usage:** `embeddings_request_start` count

---

## Additional Trackable Events

Beyond the core session/prompt/completion metrics, you can also track:

1. **Model Distribution:** Which models are most requested (`requested_model` field)
2. **Session Duration:** Actual vs requested session duration
3. **User Retention:** User activity over time windows
4. **API Key Usage:** Which keys are most active
5. **Geographic Distribution:** If client info is logged
6. **Time-of-Day Patterns:** Request distribution by hour/day
7. **Failure Modes:** Most common error types
8. **Recovery Success:** Retry success rates

---

## Notes

- All events include standard fields: `timestamp`, `level`, `logger`, `caller`
- Use `event_type` field for precise metric filtering
- Combine with other fields (e.g., `user_id`, `model`) for dimensional analysis
- Log retention should be configured based on compliance requirements
- Consider sampling for high-volume events if costs are a concern

---

## Related Documentation

- [Logging System Reference](LOGGING_SYSTEM_REFERENCE.md)
- [Logging Fixes Applied](LOGGING_FIXES_APPLIED.md)
- [src/core/logging_config.py](../src/core/logging_config.py)

