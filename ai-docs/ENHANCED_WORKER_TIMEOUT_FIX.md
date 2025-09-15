# Enhanced Worker Timeout Fix

## Problem
The API container was experiencing severe worker timeout issues where all Gunicorn workers would timeout after 30 seconds during startup, leading to a cascade of worker kills and restarts.

## Root Cause Analysis
1. **Resource Contention**: Multiple workers simultaneously accessing database and external APIs during startup
2. **Heavy Initialization**: Database migration checks and CloudFront model fetching taking too long
3. **Memory Pressure**: Workers consuming excessive memory during concurrent initialization
4. **No Timeout Protection**: Initialization tasks could hang indefinitely

## Enhanced Solution

### 1. **Intelligent Worker Selection**
```python
# Use psutil to find the actual first worker by PID
try:
    current_process = psutil.Process(worker_pid)
    parent_pid = current_process.ppid()
    parent = psutil.Process(parent_pid)
    worker_pids = [child.pid for child in parent.children() if child.pid != parent_pid]
    is_first_worker = worker_pid == min(worker_pids) if worker_pids else True
except:
    # Fallback to even more selective modulo approach
    is_first_worker = worker_pid % 8 == 0  # Only 1 in 8 workers
```

### 2. **Timeout Protection**
```python
# Run initialization with a 20-second timeout to prevent worker timeout
try:
    await asyncio.wait_for(initialization_with_timeout(), timeout=20.0)
    logger.info("✅ Worker initialization completed within timeout")
except asyncio.TimeoutError:
    logger.error("❌ Worker initialization timed out after 20 seconds")
    logger.warning("Continuing startup - services will initialize on first request")
```

### 3. **Enhanced Staggered Delays**
```python
# Add longer, variable delays to reduce resource pressure
await asyncio.sleep(1.0 + (worker_pid % 4) * 0.5)  # 1-3 second staggered delay
```

### 4. **Added Dependencies**
- **psutil**: Added to `pyproject.toml` for process management and worker selection

## Implementation Details

### Changes Made
1. **`src/main.py`**: Enhanced startup logic with intelligent worker selection and timeout protection
2. **`pyproject.toml`**: Added `psutil = "^5.9.0"` dependency

### Key Improvements
- **Single Worker Initialization**: Only the lowest PID worker performs heavy initialization
- **Timeout Safety**: 20-second timeout prevents workers from hanging during startup
- **Graceful Degradation**: If initialization fails, services initialize on first request
- **Resource Optimization**: Staggered delays reduce memory and CPU pressure
- **Fallback Strategy**: If psutil fails, falls back to modulo selection (1 in 8 workers)

## Expected Behavior

### Successful Startup
- Only one worker logs: "🗃️ This worker selected for database and service initialization..."
- Other workers log: "⏩ Skipping database/service initialization in this worker to avoid contention"
- All workers complete startup within 30-second timeout
- No worker timeout or SIGKILL messages

### Failure Scenarios
- If database is unreachable: Initialization times out gracefully, services retry on first request
- If CloudFront is unreachable: Model service initialization fails gracefully, retries on first API call
- If psutil fails: Falls back to modulo selection method

## Monitoring
- Watch for "✅ Worker initialization completed within timeout" messages
- Monitor for absence of "WORKER TIMEOUT" and "SIGKILL" messages
- Verify all workers show "Application startup complete"

## Future Considerations
- Consider implementing health check endpoint that doesn't require heavy initialization
- Explore lazy initialization patterns for non-critical services
- Monitor memory usage patterns during startup
