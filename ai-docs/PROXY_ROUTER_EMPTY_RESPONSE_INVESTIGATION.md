# Consumer Proxy-Router Empty Response Investigation

## Problem Statement

The Consumer Proxy-Router (`routerapi.dev.mor.org:8082`) is returning HTTP 200 OK with **empty response bodies** for chat completion requests, causing failures in the API Gateway.

## Architecture

```
User → API Gateway → Consumer Proxy-Router → Provider Proxy-Router → LLM Server
                     (routerapi.dev.mor.org:8082)
```

## Evidence from API Gateway Logs

```json
{
  "event": "HTTP Request: POST http://routerapi.dev.mor.org:8082/v1/chat/completions?session_id=0x827f... HTTP/1.1 200 OK",
  "error": "Expecting value: line 1 column 1 (char 0)",
  "response_text": "",
  "response_status_code": 200
}
```

**Key Facts:**
- Status code: `200 OK` ✅
- Response body: Empty `""` ❌
- Session ID: `0x827f1898506a7b6d21de61ddea953b41dc51eab91e8c780b3d33231079c5548d`
- Payload: DnD narrative (~500 chars, complex prompt)

## Investigation Steps

### 1. Check Consumer Proxy-Router Logs

**Access the proxy-router logs for this specific session:**

```bash
# For the specific session that failed
SESSION_ID="0x827f1898506a7b6d21de61ddea953b41dc51eab91e8c780b3d33231079c5548d"

# If using CloudWatch
aws logs filter-log-events \
  --log-group-name /ecs/proxy-router \
  --filter-pattern "$SESSION_ID" \
  --start-time $(date -u -d '2025-10-14 14:20:00' +%s)000 \
  --end-time $(date -u -d '2025-10-14 14:25:00' +%s)000

# If using local logs
grep "$SESSION_ID" /var/log/proxy-router/*.log
```

**Look for:**
- ❓ Timeout errors
- ❓ Provider connection failures  
- ❓ Empty response received from Provider Proxy-Router
- ❓ Request forwarding success/failure

### 2. Check Provider Proxy-Router Connection

**From Consumer Proxy-Router, check if it can reach Provider:**

```bash
# Test connectivity to provider
curl -v http://<provider-proxy-router>:8082/health

# Check if session exists on provider side
curl http://<provider-proxy-router>:8082/v1/blockchain/sessions/$SESSION_ID
```

**Possible issues:**
- Network timeout between Consumer ↔ Provider
- Provider Proxy-Router is down
- Provider Proxy-Router is rejecting requests

### 3. Check LLM Server Status

**From Provider Proxy-Router logs, check:**

```bash
# Look for this session on provider side
grep "$SESSION_ID" /var/log/provider-proxy-router/*.log

# Check for timeout errors
grep -i "timeout" /var/log/provider-proxy-router/*.log | tail -20

# Check for LLM server connection errors
grep -i "connection refused\|connection reset" /var/log/provider-proxy-router/*.log | tail -20
```

**Red flags:**
- LLM server not responding
- LLM server returning errors
- Timeout before LLM generates response

### 4. Analyze Request Characteristics

**For this specific failed request:**

```json
{
  "messages": [
    {"role": "system", "content": ""},
    {"role": "user", "content": "You are a dungeon master in a 5e DnD campaign... (~1500+ chars)"}
  ],
  "session_id": "0x827f1898506a7b6d21de61ddea953b41dc51eab91e8c780b3d33231079c5548d"
}
```

**Analyze:**
- Total token count of prompt
- Model's context window limit
- Estimated generation time for complex narrative

```bash
# Count approximate tokens (rough estimate: 1 token ≈ 4 chars)
echo "Total chars: 1500"
echo "Approximate tokens: $((1500 / 4)) = ~375 input tokens"

# For complex narratives, generation could take 30-180 seconds
```

## Likely Root Causes (Prioritized)

### 🥇 **Most Likely: LLM Server Timeout (60-70% probability)**

**Symptoms:**
- Happens with complex/long prompts
- Inconsistent (sometimes works, sometimes doesn't)
- Response time > timeout threshold

**Why it returns 200 with empty body:**
```
1. Consumer Proxy forwards request to Provider Proxy
2. Provider Proxy forwards to LLM Server  
3. LLM Server starts generation (slow for complex prompt)
4. Provider Proxy times out waiting for response
5. Provider Proxy returns 200 OK with empty body (BUG)
6. Consumer Proxy forwards 200 + empty body to API Gateway
```

**Fix Location:** Provider Proxy-Router or LLM Server
- Increase timeout thresholds
- Add proper error handling for timeouts
- Return 504 Gateway Timeout instead of 200 OK

**Testing:**
```bash
# Test with simpler prompt
curl -X POST http://routerapi.dev.mor.org:8082/v1/chat/completions?session_id=$SESSION_ID \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }'

# If simple prompt works, it's a timeout issue
```

### 🥈 **Second Most Likely: Provider Proxy-Router Bug (20-25% probability)**

**Symptoms:**
- Provider Proxy-Router has bug in response handling
- Returns 200 OK when upstream fails
- Missing error handling for empty responses

**Check Provider Proxy-Router code:**
```go
// Look for code like this (BAD PATTERN):
func handleChatCompletion(w http.ResponseWriter, r *http.Request) {
    resp, err := llmClient.Generate(prompt)
    if err != nil {
        // BUG: Returns 200 even on error
        w.WriteHeader(200)
        return
    }
    json.NewEncoder(w).Encode(resp)
}
```

**Fix:** Update Provider Proxy-Router error handling

### 🥉 **Third: LLM Server Resource Issues (10-15% probability)**

**Symptoms:**
- LLM server out of memory
- Model crashes during generation
- Server returns empty response instead of error

**Check LLM Server logs:**
```bash
# Check for OOM kills
dmesg | grep -i "out of memory"
journalctl | grep -i "killed process"

# Check model server logs
grep -i "error\|exception\|failed" /var/log/llm-server/*.log
```

### 🎯 **Other Possibilities (5% probability)**

- Token limit exceeded (model silently refuses)
- Rate limiting (model provider returns 200 instead of 429)
- Network partition between proxies
- Load balancer dropping responses

## Immediate Workarounds

### 1. Add Retry Logic in API Gateway ✅

Already implemented in `chat_non_streaming.py` - will retry with new session on empty response.

### 2. Add Response Validation in Proxy Service ✅

Just implemented in `proxy_router_service.py` - will detect empty responses early.

### 3. Reduce Timeout Sensitivity

For complex prompts, consider:
```python
# In proxy_router_service.py chatCompletions()
timeout=300.0,  # 5 minutes instead of 3 minutes for complex prompts
```

## Long-Term Fixes

### 1. Fix Consumer Proxy-Router Response Handling

**Add validation before returning:**
```go
func forwardToProvider(req *Request) (*Response, error) {
    resp, err := providerClient.Do(req)
    if err != nil {
        return nil, fmt.Errorf("provider error: %w", err)
    }
    
    // VALIDATE RESPONSE
    if resp.StatusCode == 200 && len(resp.Body) == 0 {
        return nil, fmt.Errorf("provider returned empty response")
    }
    
    return resp, nil
}
```

### 2. Fix Provider Proxy-Router Timeout Handling

**Return proper error codes:**
```go
func callLLMServer(prompt string) (*Response, error) {
    ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
    defer cancel()
    
    resp, err := llmClient.Generate(ctx, prompt)
    if err != nil {
        if errors.Is(err, context.DeadlineExceeded) {
            // Return 504 Gateway Timeout, not 200 OK
            return nil, &TimeoutError{
                StatusCode: 504,
                Message: "LLM server timeout"
            }
        }
        return nil, err
    }
    
    return resp, nil
}
```

### 3. Add Circuit Breaker

If LLM server is consistently timing out:
```python
# In session_service.py
if error_rate > 50% for last 10 requests:
    mark_provider_unhealthy()
    route_to_fallback_provider()
```

## Monitoring & Alerts

### CloudWatch Metrics to Add

1. **Empty Response Rate**
   ```
   Metric: proxy_empty_response_count
   Alert: > 5 in 5 minutes
   ```

2. **Proxy Response Time**
   ```
   Metric: proxy_response_time_p99
   Alert: > 180 seconds
   ```

3. **LLM Server Health**
   ```
   Metric: llm_server_timeout_rate
   Alert: > 10% of requests
   ```

## Next Steps

1. ✅ **Deploy API Gateway fixes** (response validation added)
2. 🔍 **Check Consumer Proxy-Router logs** for session `0x827f...`
3. 🔍 **Check Provider Proxy-Router logs** for the same session
4. 🔍 **Check LLM Server logs** for timeout/error patterns
5. 🛠️ **Fix root cause** in Consumer or Provider Proxy-Router
6. 📊 **Add monitoring** for empty response tracking

## Example: Good vs Bad Proxy Behavior

### ❌ Bad (Current Behavior)
```
Provider timeout → Provider returns 200 + empty body → Consumer forwards 200 + empty body
```

### ✅ Good (Expected Behavior)  
```
Provider timeout → Provider returns 504 Gateway Timeout → Consumer forwards 504 → API Gateway returns 502
```

---

**Investigation Priority:**
1. Consumer Proxy-Router logs (most likely to show the issue)
2. Provider Proxy-Router logs (next most likely)
3. LLM Server logs (if above shows forwarding is working)
4. Network connectivity between components

**Expected Outcome:**
Find timeout, connection error, or bug in proxy router that causes 200 OK with empty body.

