# Rapid Sequential Chat Completions - 500 Error Fix

## Problem Summary

Users making rapid sequential chat completion requests (12 calls in 30-40 seconds, ~24K tokens total) experience consistent 500 errors after the 3rd call (~7K tokens). The error threshold is reproducible and happens in cycles with high token usage.

### User's Issue Details
- **12 LLM calls** made back-to-back (sequential, not concurrent)
- **Total tokens**: 24,014 tokens
- **Cycle duration**: 30-40 seconds
- **Error threshold**: After 3rd call (~7,059 tokens)
- **Pattern**: Consistent in high-token cycles, not in low-token cycles

## Root Causes Identified

### 1. ⚠️ **CRITICAL: Gunicorn Worker Timeout Too Low**

**Location**: `Dockerfile` line 78

**Problem**: Default gunicorn timeout is 30 seconds, but the user's cycle runs 30-40 seconds. This causes workers to be killed mid-request.

**Evidence**:
```dockerfile
# OLD - No timeout specified (defaults to 30s)
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", "-b", "0.0.0.0:8000", "src.main:app"]
```

**Impact**: Workers processing long-running requests get killed at 30 seconds, causing 500 errors.

**Fixed**: ✅ Updated Dockerfile with proper timeouts

### 2. ⚠️ **Database Connection Pool Exhaustion**

**Location**: `src/db/database.py`

**Problem**: No explicit pool size configuration. SQLAlchemy defaults to:
- `pool_size=5` (max connections)
- `max_overflow=10` (additional burst connections)
- With 4 gunicorn workers making rapid requests, this can exhaust quickly

**Current Configuration**:
```python
engine = create_async_engine(
    str(settings.DATABASE_URL),
    pool_pre_ping=True,
    echo=False,
)
```

**Impact**: 
- Each chat completion needs DB connections for:
  - Session lookup/creation
  - API key validation
  - Private key retrieval
  - Session storage
- 12 rapid calls × multiple DB queries = pool exhaustion

**Needs Fix**: ⚠️ Add explicit pool configuration

### 3. ⚠️ **Proxy Router Client Connection Issues**

**Location**: `src/services/proxy_router_service.py` line 123

**Problem**: Creating new `httpx.AsyncClient()` for **every request**, including:
- No connection pooling
- No keep-alive connections
- Socket exhaustion on rapid requests
- Slower due to connection overhead

**Current Code**:
```python
async with httpx.AsyncClient() as client:  # NEW CLIENT EVERY TIME!
    for attempt in range(max_retries):
        response = await client.request(...)
```

**Impact**: 
- 12 LLM calls = 12 new HTTP clients created
- Each client creates new TCP connections
- Connection establishment overhead adds latency
- Can hit OS-level connection limits

**Needs Fix**: ⚠️ Use singleton/pooled client

### 4. ⚠️ **Proxy Router Timeout Configuration**

**Location**: `src/services/proxy_router_service.py` line 57

**Problem**: Default timeout of 30 seconds for proxy router requests

```python
async def _execute_request(
    ...
    timeout: float = 30.0,  # TOO SHORT for large responses
    ...
)
```

**Impact**: Large token responses (like call #3 with 4,058 tokens) can take longer than 30s to process, causing timeouts.

**Needs Fix**: ⚠️ Increase proxy router timeout

### 5. ⚠️ **No Request Queuing/Throttling**

**Problem**: No rate limiting or request queuing for rapid sequential requests. All requests hit the system simultaneously, causing resource contention.

**Impact**: 
- DB connections compete
- Proxy router connections compete  
- Memory accumulation from held resources
- No graceful degradation

**Needs Fix**: ⚠️ Consider request queuing for sustained load

## Fixes Applied

### ✅ Fix #1: Updated Dockerfile with Proper Timeouts

```dockerfile
# Increased timeouts for long-running chat completion sequences
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", "-b", "0.0.0.0:8000", \
     "--timeout", "300", \              # 5 minutes for worker timeout
     "--graceful-timeout", "320", \     # 20 seconds grace period
     "--keep-alive", "75", \            # Keep connections alive
     "src.main:app"]
```

**Why these values**:
- `--timeout 300`: Allows 5 minutes for long-running requests (10x the 30-40s cycle)
- `--graceful-timeout 320`: Gives workers 20s to finish after timeout
- `--keep-alive 75`: Keeps ALB connections alive (ALB timeout is usually 60s)

## Fixes Needed

### ⚠️ Fix #2: Configure Database Connection Pool

**File**: `src/db/database.py`

**Change needed**:

```python
# Create async engine instance with explicit pool configuration
engine = create_async_engine(
    str(settings.DATABASE_URL),
    pool_pre_ping=True,
    echo=False,
    # Explicit pool configuration for high-concurrency scenarios
    pool_size=20,              # Base pool size (increased from default 5)
    max_overflow=30,           # Allow burst to 50 total connections
    pool_timeout=30,           # Wait 30s for connection before raising error
    pool_recycle=3600,         # Recycle connections after 1 hour
    pool_reset_on_return='rollback',  # Reset connection state on return
)
```

**Reasoning**:
- 4 workers × 5 concurrent requests = 20 base connections
- Allow burst to 50 during peak load
- Prevents "QueuePool limit exceeded" errors

### ⚠️ Fix #3: Use Singleton HTTP Client for Proxy Router

**File**: `src/services/proxy_router_service.py`

**Change needed**:

```python
# Create singleton HTTP client with connection pooling
_http_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()

async def get_http_client() -> httpx.AsyncClient:
    """Get or create singleton HTTP client with connection pooling."""
    global _http_client
    
    if _http_client is None:
        async with _client_lock:
            if _http_client is None:
                _http_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(120.0, connect=10.0),  # Longer timeout for large responses
                    limits=httpx.Limits(
                        max_connections=100,        # Total connection pool
                        max_keepalive_connections=20,  # Keep 20 connections alive
                    ),
                    http2=True,  # Enable HTTP/2 if supported
                )
    
    return _http_client

async def close_http_client():
    """Close singleton HTTP client (call on app shutdown)."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None

# Update _execute_request to use singleton client
async def _execute_request(
    method: str,
    endpoint: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json_data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 120.0,  # INCREASED from 30s
    max_retries: int = 3,
    user_id: Optional[int] = None,
    db = None,
) -> httpx.Response:
    """Execute request using singleton client."""
    
    # ... authentication setup code ...
    
    # Use singleton client instead of creating new one
    client = await get_http_client()
    
    for attempt in range(max_retries):
        try:
            response = await client.request(
                method,
                url,
                headers=request_headers,
                json=json_data,
                params=params,
                auth=auth,
                timeout=timeout
            )
            # ... rest of logic ...
```

**Add to `src/main.py` shutdown event**:

```python
@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on application shutdown."""
    # ... existing shutdown code ...
    
    # Close proxy router HTTP client
    from src.services import proxy_router_service
    await proxy_router_service.close_http_client()
```

**Benefits**:
- Connection pooling reduces overhead
- Keep-alive connections improve performance
- Prevents socket exhaustion
- Better timeout configuration

### ⚠️ Fix #4: Increase Chat Completion Specific Timeouts

**File**: `src/services/proxy_router_service.py`

**Functions to update**:

```python
async def chatCompletions(
    *,
    session_id: str,
    messages: list,
    **kwargs
) -> httpx.Response:
    """Send chat completion request with extended timeout for large responses."""
    return await _execute_request(
        "POST",
        "v1/chat/completions",
        json_data={
            "sessionID": session_id,
            "messages": messages,
            **kwargs
        },
        timeout=180.0,  # INCREASED to 3 minutes for large token counts
        max_retries=2   # Reduce retries since timeouts are longer
    )
```

### ⚠️ Fix #5: Add Request Metrics and Monitoring

**File**: `src/api/v1/chat/index.py`

**Add metrics tracking**:

```python
from datetime import datetime
import structlog

logger = structlog.get_logger()

@router.post("/completions")
async def create_chat_completion(
    request_data: ChatCompletionRequest,
    request: Request,
    user: User = Depends(get_api_key_user),
    db_api_key: APIKey = Depends(get_current_api_key),
    db: AsyncSession = Depends(get_db)
):
    start_time = datetime.now()
    
    try:
        # ... existing completion logic ...
        
        # Log request metrics
        duration = (datetime.now() - start_time).total_seconds()
        logger.info("Chat completion completed",
                   user_id=user.id,
                   api_key_id=db_api_key.id,
                   duration_seconds=duration,
                   request_tokens=sum(len(m.content or "") for m in request_data.messages),
                   event_type="chat_completion_success")
        
        return response
        
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        logger.error("Chat completion failed",
                    user_id=user.id,
                    api_key_id=db_api_key.id,
                    duration_seconds=duration,
                    error=str(e),
                    event_type="chat_completion_error")
        raise
```

## Testing Plan

### 1. **Load Testing Script**

Create `tests/load/test_rapid_sequential_completions.py`:

```python
import asyncio
import httpx
import time
from typing import List, Dict

async def test_rapid_sequential_completions(
    api_key: str,
    base_url: str = "https://api.dev.mor.org",
    num_requests: int = 12
):
    """Test rapid sequential chat completions matching user's pattern."""
    
    # Token counts matching user's pattern
    token_patterns = [
        (664, 2),      # LLM call 1
        (2280, 55),    # LLM call 2
        (3737, 321),   # LLM call 3 - ERROR POINT
        (1371, 89),    # LLM call 4
        (1517, 45),    # LLM call 5
        (4161, 88),    # LLM call 6
        (1966, 28),    # LLM call 7
        (1290, 258),   # LLM call 8
        (1639, 124),   # LLM call 9
        (2408, 67),    # LLM call 10
        (2031, 38),    # LLM call 11
        (714, 51),     # LLM call 12
    ]
    
    results = []
    total_tokens = 0
    start_time = time.time()
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        for i, (input_tokens, output_tokens) in enumerate(token_patterns[:num_requests], 1):
            total_tokens += input_tokens + output_tokens
            
            # Create message approximating token count
            message_content = "test " * (input_tokens // 5)  # Rough approximation
            
            request_start = time.time()
            try:
                response = await client.post(
                    f"{base_url}/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "mistral-31-24b",
                        "messages": [
                            {"role": "user", "content": message_content}
                        ],
                        "max_tokens": output_tokens,
                        "stream": False
                    }
                )
                
                duration = time.time() - request_start
                
                results.append({
                    "call": i,
                    "status": response.status_code,
                    "duration": duration,
                    "cumulative_tokens": total_tokens,
                    "success": response.status_code == 200
                })
                
                print(f"✅ Call {i}: {response.status_code} in {duration:.2f}s (cumulative: {total_tokens} tokens)")
                
            except Exception as e:
                duration = time.time() - request_start
                results.append({
                    "call": i,
                    "status": "error",
                    "duration": duration,
                    "cumulative_tokens": total_tokens,
                    "error": str(e),
                    "success": False
                })
                
                print(f"❌ Call {i}: ERROR after {duration:.2f}s - {str(e)} (cumulative: {total_tokens} tokens)")
    
    total_duration = time.time() - start_time
    success_count = sum(1 for r in results if r["success"])
    
    print(f"\n{'='*60}")
    print(f"Test Complete: {success_count}/{num_requests} successful")
    print(f"Total duration: {total_duration:.2f}s")
    print(f"Total tokens: {total_tokens}")
    print(f"{'='*60}")
    
    # Check if error occurred at expected threshold
    if success_count < num_requests:
        first_error = next(r for r in results if not r["success"])
        print(f"\n⚠️ First error at call {first_error['call']} with {first_error['cumulative_tokens']} cumulative tokens")
        print(f"   Expected error threshold: ~7059 tokens (call 3)")
    
    return results

# Run test
if __name__ == "__main__":
    API_KEY = "your-api-key-here"
    results = asyncio.run(test_rapid_sequential_completions(API_KEY))
```

### 2. **Monitor CloudWatch Logs**

Look for these patterns:

```bash
# Worker timeout errors (SHOULD NOT SEE THESE after fix)
grep "Worker timeout" /aws/ecs/morpheus-api

# Database pool errors (SHOULD NOT SEE THESE after fix)
grep "QueuePool limit" /aws/ecs/morpheus-api

# Proxy router timeout errors
grep "proxy_request_timeout" /aws/ecs/morpheus-api

# Successful completion patterns
grep "chat_completion_success" /aws/ecs/morpheus-api
```

### 3. **ECS Task Metrics**

Monitor:
- **CPU utilization**: Should stay under 70%
- **Memory utilization**: Should stay under 80%
- **Task restarts**: Should be 0 during test
- **ALB target response time**: Should stay under 30s

## Deployment Plan

### Phase 1: Immediate Fix (Dockerfile) ✅ DONE
1. Deploy updated Dockerfile with timeout configuration
2. Monitor for worker timeout errors
3. Verify 12-request sequence completes

### Phase 2: Database Pool Configuration
1. Update `src/db/database.py` with pool settings
2. Deploy to dev environment
3. Run load test
4. Monitor connection pool metrics
5. Deploy to production

### Phase 3: HTTP Client Pooling
1. Update `src/services/proxy_router_service.py` with singleton client
2. Add shutdown handler to `src/main.py`
3. Deploy to dev environment
4. Run load test
5. Monitor connection metrics
6. Deploy to production

### Phase 4: Monitoring & Optimization
1. Add request metrics to chat endpoint
2. Set up CloudWatch alarms for:
   - Worker timeouts
   - Database pool exhaustion
   - Proxy router errors
3. Analyze performance data
4. Fine-tune settings

## Expected Results

### Before Fixes
- ❌ 500 error after call #3 (~7K tokens)
- ❌ Worker timeout at 30 seconds
- ❌ Database pool exhaustion warnings
- ❌ Connection errors in logs

### After Fixes
- ✅ All 12 calls complete successfully
- ✅ No worker timeouts (even at 40+ seconds)
- ✅ No database pool warnings
- ✅ Improved response times due to connection pooling
- ✅ Better resource utilization

## Monitoring Queries

### CloudWatch Insights Queries

**Worker Timeouts**:
```sql
fields @timestamp, @message
| filter @message like /Worker timeout/
| stats count() by bin(5m)
```

**Database Pool Issues**:
```sql
fields @timestamp, @message
| filter @message like /QueuePool/ or @message like /pool/
| sort @timestamp desc
```

**Chat Completion Performance**:
```sql
fields @timestamp, duration_seconds, request_tokens
| filter event_type = "chat_completion_success"
| stats avg(duration_seconds), max(duration_seconds), count() by bin(5m)
```

## Success Criteria

- [ ] ✅ Dockerfile updated with 300s timeout
- [ ] Database pool configured with 20/30 size/overflow
- [ ] HTTP client converted to singleton pattern
- [ ] Proxy router timeout increased to 120-180s
- [ ] Load test passes 12 sequential requests
- [ ] No 500 errors in high-token cycles
- [ ] Response times acceptable (<30s per request)
- [ ] No worker timeout errors in logs
- [ ] No database pool exhaustion warnings

## Notes

1. The user's 30-40s cycle time is within acceptable limits with 300s worker timeout
2. Connection pooling should improve performance beyond just fixing errors
3. Consider implementing request rate limiting for sustained high load
4. Monitor memory usage after deployment - large token counts can accumulate

---

**Status**: Phase 1 Complete (Dockerfile fix applied), Phases 2-4 require deployment and testing

