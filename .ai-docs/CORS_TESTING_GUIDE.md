# CORS Testing Guide for ALB lb_cookie Stickiness

This guide provides comprehensive testing instructions for verifying that CORS is properly configured to support AWS ALB sticky sessions with credentials.

## Quick Verification

### 1. Basic CORS Check
```bash
# Test from openbeta.mor.org origin
curl -H "Origin: https://openbeta.mor.org" \
     -v \
     https://api.mor.org/cors-check

# Test from api.mor.org origin  
curl -H "Origin: https://api.mor.org" \
     -v \
     https://api.mor.org/cors-check
```

**Expected Response Headers:**
```
Access-Control-Allow-Origin: https://openbeta.mor.org
Access-Control-Allow-Credentials: true
Vary: Origin
```

### 2. Preflight OPTIONS Request
```bash
# Test preflight for POST request with credentials
curl -X OPTIONS \
     -H "Origin: https://openbeta.mor.org" \
     -H "Access-Control-Request-Method: POST" \
     -H "Access-Control-Request-Headers: Authorization,Content-Type" \
     -v \
     https://api.mor.org/api/v1/models

# Test preflight for chat completions
curl -X OPTIONS \
     -H "Origin: https://openbeta.mor.org" \
     -H "Access-Control-Request-Method: POST" \
     -H "Access-Control-Request-Headers: Authorization,Content-Type,X-API-Key" \
     -v \
     https://api.mor.org/api/v1/chat/completions
```

**Expected Preflight Response Headers:**
```
HTTP/1.1 204 No Content
Access-Control-Allow-Origin: https://openbeta.mor.org
Access-Control-Allow-Credentials: true
Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
Access-Control-Allow-Headers: Authorization, Content-Type, X-Requested-With, X-API-Key
Access-Control-Max-Age: 86400
Vary: Origin
```

### 3. Actual API Request with Credentials
```bash
# Test actual API request (will fail auth but CORS should work)
curl -X POST \
     -H "Origin: https://openbeta.mor.org" \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer test-token" \
     -d '{"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "test"}]}' \
     -v \
     https://api.mor.org/api/v1/chat/completions
```

**Expected Response Headers (even with 401/403 auth error):**
```
Access-Control-Allow-Origin: https://openbeta.mor.org
Access-Control-Allow-Credentials: true
Access-Control-Expose-Headers: Content-Length, Content-Type
Vary: Origin
```

## Comprehensive Testing

### 4. Test All Allowed Origins
```bash
# Test each configured origin
ORIGINS=("https://openbeta.mor.org" "https://api.mor.org")

for origin in "${ORIGINS[@]}"; do
    echo "Testing origin: $origin"
    curl -H "Origin: $origin" \
         -v \
         https://api.mor.org/cors-check \
         2>&1 | grep -E "(Access-Control|Vary|HTTP/)"
    echo "---"
done
```

### 5. Test Different Origin Types
```bash
# Test various origin types to understand the new dynamic handling

# Explicit origins (always allowed)
echo "=== Testing Explicit Origins ==="
curl -H "Origin: https://openbeta.mor.org" -v https://api.mor.org/cors-check 2>&1 | grep -E "(Access-Control|HTTP/)"

# Trusted pattern origins (*.mor.org subdomains)
echo "=== Testing Trusted Pattern Origins ==="
curl -H "Origin: https://subdomain.mor.org" -v https://api.mor.org/cors-check 2>&1 | grep -E "(Access-Control|HTTP/)"
curl -H "Origin: https://test.dev.mor.org" -v https://api.mor.org/cors-check 2>&1 | grep -E "(Access-Control|HTTP/)"

# Direct HTTPS access (any HTTPS origin for ALB stickiness)
echo "=== Testing Direct HTTPS Access ==="
curl -H "Origin: https://example.com" -v https://api.mor.org/cors-check 2>&1 | grep -E "(Access-Control|HTTP/)"
curl -H "Origin: https://google.com" -v https://api.mor.org/cors-check 2>&1 | grep -E "(Access-Control|HTTP/)"

# HTTP origins (should be blocked except localhost)
echo "=== Testing HTTP Origins ==="
curl -H "Origin: http://example.com" -v https://api.mor.org/cors-check 2>&1 | grep -E "(Access-Control|HTTP/)"
curl -H "Origin: http://localhost:3000" -v https://api.mor.org/cors-check 2>&1 | grep -E "(Access-Control|HTTP/)"
```

**Expected:** 
- ✅ All HTTPS origins get CORS headers (for ALB cookie stickiness)
- ❌ HTTP origins blocked (except localhost for development)
- ✅ All allowed origins get `Access-Control-Allow-Credentials: true`

### 6. Browser Testing

#### JavaScript Console Test
Open browser console on `https://openbeta.mor.org` and run:

```javascript
// Test CORS with credentials
fetch('https://api.mor.org/cors-check', {
    method: 'GET',
    credentials: 'include',  // This is key for ALB lb_cookie
    headers: {
        'Content-Type': 'application/json'
    }
})
.then(response => {
    console.log('CORS Success:', response.status);
    console.log('Headers:', [...response.headers.entries()]);
    return response.json();
})
.then(data => console.log('Response:', data))
.catch(error => console.error('CORS Error:', error));
```

#### Network Tab Verification
1. Open browser dev tools (F12)
2. Go to Network tab
3. Navigate to `https://openbeta.mor.org`
4. Run the JavaScript test above
5. Check the request in Network tab for these headers:

**Request Headers:**
```
Origin: https://openbeta.mor.org
```

**Response Headers:**
```
Access-Control-Allow-Origin: https://openbeta.mor.org
Access-Control-Allow-Credentials: true
Vary: Origin
```

### 7. ALB Cookie Stickiness Test

```bash
# Test with cookie to verify ALB stickiness works
curl -H "Origin: https://openbeta.mor.org" \
     -H "Cookie: AWSALB=test-session-cookie" \
     -v \
     https://api.mor.org/cors-check
```

**Expected:** Same CORS headers as before, cookie should be preserved.

## Integration Tests

### 8. Full Authentication Flow Test

```bash
# 1. Test preflight for auth endpoint
curl -X OPTIONS \
     -H "Origin: https://openbeta.mor.org" \
     -H "Access-Control-Request-Method: POST" \
     -H "Access-Control-Request-Headers: Content-Type" \
     -v \
     https://api.mor.org/api/v1/auth/keys

# 2. Test actual auth request (will fail without valid JWT)
curl -X POST \
     -H "Origin: https://openbeta.mor.org" \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer invalid-token" \
     -d '{"name": "test-key"}' \
     -v \
     https://api.mor.org/api/v1/auth/keys
```

### 9. Models Endpoint Test

```bash
# Test models endpoint with CORS
curl -H "Origin: https://openbeta.mor.org" \
     -v \
     https://api.mor.org/api/v1/models
```

## Automated Test Script

Create `test_cors.sh`:

```bash
#!/bin/bash

API_BASE="https://api.mor.org"
ALLOWED_ORIGINS=("https://openbeta.mor.org" "https://api.mor.org")
DISALLOWED_ORIGINS=("https://evil.com" "http://localhost:3000")

echo "🧪 CORS Testing Suite for ALB lb_cookie Stickiness"
echo "=================================================="

# Test 1: CORS Check Endpoint
echo "📋 Test 1: CORS Check Endpoint"
for origin in "${ALLOWED_ORIGINS[@]}"; do
    echo "  Testing allowed origin: $origin"
    response=$(curl -s -H "Origin: $origin" -v "$API_BASE/cors-check" 2>&1)
    
    if echo "$response" | grep -q "Access-Control-Allow-Origin: $origin"; then
        echo "  ✅ CORS headers present"
    else
        echo "  ❌ CORS headers missing"
    fi
    
    if echo "$response" | grep -q "Access-Control-Allow-Credentials: true"; then
        echo "  ✅ Credentials allowed"
    else
        echo "  ❌ Credentials not allowed"
    fi
    
    if echo "$response" | grep -q "Vary: Origin"; then
        echo "  ✅ Vary: Origin header present"
    else
        echo "  ❌ Vary: Origin header missing"
    fi
    echo ""
done

# Test 2: Preflight Requests
echo "📋 Test 2: Preflight OPTIONS Requests"
for origin in "${ALLOWED_ORIGINS[@]}"; do
    echo "  Testing preflight for origin: $origin"
    response=$(curl -s -X OPTIONS \
        -H "Origin: $origin" \
        -H "Access-Control-Request-Method: POST" \
        -H "Access-Control-Request-Headers: Authorization,Content-Type" \
        -v "$API_BASE/api/v1/models" 2>&1)
    
    if echo "$response" | grep -q "HTTP/1.1 204\|HTTP/2 204"; then
        echo "  ✅ Preflight returns 204"
    else
        echo "  ❌ Preflight failed"
    fi
    echo ""
done

# Test 3: Disallowed Origins
echo "📋 Test 3: Disallowed Origins"
for origin in "${DISALLOWED_ORIGINS[@]}"; do
    echo "  Testing disallowed origin: $origin"
    response=$(curl -s -H "Origin: $origin" -v "$API_BASE/cors-check" 2>&1)
    
    if echo "$response" | grep -q "Access-Control-Allow-Origin:"; then
        echo "  ❌ CORS headers present (should be blocked)"
    else
        echo "  ✅ CORS headers correctly blocked"
    fi
    echo ""
done

echo "🎉 CORS testing complete!"
```

Make it executable and run:
```bash
chmod +x test_cors.sh
./test_cors.sh
```

## Troubleshooting

### Common Issues

1. **Wildcard with Credentials Error**
   ```
   Cannot use wildcard '*' in allowed_origins when allow_credentials=True
   ```
   **Solution:** Use explicit origins in `CORS_ALLOWED_ORIGINS`

2. **No CORS Headers**
   - Check that origin is in `CORS_ALLOWED_ORIGINS`
   - Verify middleware is loaded correctly
   - Check server logs for errors

3. **Preflight Fails**
   - Ensure OPTIONS method is allowed
   - Check `Access-Control-Request-Headers` match allowed headers
   - Verify `Access-Control-Max-Age` is set

4. **Cache Poisoning**
   - Ensure `Vary: Origin` header is present
   - Check CDN/proxy configuration

### Environment Variables

```bash
# Production
CORS_ALLOWED_ORIGINS="https://openbeta.mor.org,https://api.mor.org"

# Development (local testing)
CORS_ALLOWED_ORIGINS="https://openbeta.mor.org,https://api.mor.org,http://localhost:3000"

# Never use in production with credentials
CORS_ALLOWED_ORIGINS="*"  # ❌ DANGEROUS
```

## Security Checklist

- [ ] No wildcard (`*`) origins with credentials enabled
- [ ] Only trusted domains in allowed origins
- [ ] `Vary: Origin` header present on all responses
- [ ] Preflight requests return 204 status
- [ ] Credentials properly enabled for ALB stickiness
- [ ] Exposed headers limited to necessary ones
- [ ] Max-Age set appropriately (24 hours)

## Production Deployment

1. Set environment variable:
   ```bash
   export CORS_ALLOWED_ORIGINS="https://openbeta.mor.org,https://api.mor.org"
   ```

2. Verify configuration:
   ```bash
   curl -H "Origin: https://openbeta.mor.org" -v https://api.mor.org/cors-check
   ```

3. Test ALB stickiness:
   ```bash
   # Make request with cookie
   curl -H "Origin: https://openbeta.mor.org" \
        -H "Cookie: AWSALB=session-id" \
        -v https://api.mor.org/cors-check
   ```

4. Monitor logs for CORS errors
5. Test from actual frontend application

The CORS configuration is now ready for production use with ALB lb_cookie stickiness! 🎉
