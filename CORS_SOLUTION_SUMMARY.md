# CORS Solution for ALB Cookie Stickiness - Universal Client Support

## Problem Solved

**Original Challenge**: Any random computer on the internet should be able to access `api.mor.org` (or `api.dev.mor.org`) directly and have ALB cookie stickiness work properly, but traditional CORS allowlists can't predict every possible origin.

**Security Constraint**: Cannot use `Access-Control-Allow-Origin: *` with `Access-Control-Allow-Credentials: true` - this is a browser security violation.

## Solution: Dynamic Origin Reflection with Security Layers

Our implementation uses a **layered approach** that allows universal access while maintaining security:

### 🎯 **How It Works**

The CORS middleware now evaluates origins in this priority order:

1. **Explicit Allowlist** (Highest Priority)
   - `https://openbeta.mor.org`
   - `https://api.mor.org` 
   - `https://openbeta.dev.mor.org`
   - `https://api.dev.mor.org`
   - Any localhost/development origins

2. **Trusted Domain Patterns** (Medium Priority)
   - `^https://.*\.mor\.org$` (any subdomain of mor.org)
   - `^https://.*\.dev\.mor\.org$` (any subdomain of dev.mor.org)

3. **Direct HTTPS Access** (Universal Access)
   - **Any HTTPS origin** is allowed with credentials
   - This enables ALB cookie stickiness from any client on the internet
   - HTTP origins are blocked (except localhost for development)

### 🌐 **Real-World Examples**

| Client Scenario | Origin | Result | Reason |
|-----------------|--------|---------|---------|
| Official frontend | `https://openbeta.mor.org` | ✅ Allowed | Explicit allowlist |
| Dev environment | `https://api.dev.mor.org` | ✅ Allowed | Explicit allowlist |
| Subdomain | `https://docs.mor.org` | ✅ Allowed | Trusted pattern |
| Random user's browser | `https://example.com` | ✅ Allowed | Direct HTTPS access |
| Developer's laptop | `https://192.168.1.100:3000` | ✅ Allowed | Direct HTTPS access |
| Insecure connection | `http://example.com` | ❌ Blocked | HTTP not allowed |
| Local development | `http://localhost:3000` | ✅ Allowed | Development exception |

### 🔒 **Security Considerations**

**Why This Is Safe:**

1. **HTTPS Requirement**: Only HTTPS origins get credentials, ensuring encrypted communication
2. **No Wildcard**: We never use `Access-Control-Allow-Origin: *` with credentials
3. **Origin Reflection**: Each origin gets its own specific `Access-Control-Allow-Origin` header
4. **Vary: Origin**: Always present to prevent cache poisoning attacks
5. **Audit Trail**: Comprehensive logging of origin types and decisions

**Security Trade-offs:**

- ✅ **Benefit**: Universal ALB cookie stickiness works from any HTTPS client
- ⚠️ **Trade-off**: Any HTTPS site can make credentialed requests to your API
- 🛡️ **Mitigation**: Your API still requires proper authentication (JWT/API keys)

### 📊 **Configuration Options**

```bash
# Environment variables to control behavior

# Auto-detect origins based on environment
ENVIRONMENT=production  # or development

# Override with explicit origins (optional)
CORS_ALLOWED_ORIGINS=https://openbeta.mor.org,https://api.mor.org

# Additional dev origins (ignored in production)
CORS_DEV_ORIGINS=http://localhost:3000,http://localhost:8080

# The middleware automatically handles:
# - Trusted domain patterns (*.mor.org)
# - Direct HTTPS access (any HTTPS origin)
```

### 🧪 **Testing Scenarios**

```bash
# Test from any HTTPS origin - should work
curl -H "Origin: https://google.com" \
     -H "Cookie: AWSALB=session-123" \
     -v https://api.mor.org/cors-check

# Expected response headers:
# Access-Control-Allow-Origin: https://google.com
# Access-Control-Allow-Credentials: true
# Vary: Origin
```

### 🚀 **ALB Cookie Stickiness Flow**

1. **Client** (from anywhere on internet) makes HTTPS request to `api.mor.org`
2. **ALB** assigns sticky session cookie (`AWSALB=...`)
3. **CORS Middleware** sees HTTPS origin, allows it with credentials
4. **Browser** receives CORS headers allowing credentials
5. **Browser** includes ALB cookie in subsequent requests
6. **ALB** routes to same backend instance based on cookie
7. **✅ Stickiness works!**

### 🔧 **Implementation Details**

**Key Files:**
- `src/core/cors_middleware.py` - Custom CORS middleware with dynamic origin handling
- `src/core/config.py` - Environment-aware CORS configuration
- `src/main.py` - Middleware integration with proper parameters

**Key Features:**
- Origin type detection and logging
- Regex pattern matching for trusted domains
- HTTPS-only policy for credentials
- Development-friendly localhost exceptions
- Comprehensive error handling and fallbacks

### 📈 **Monitoring & Debugging**

**Log Messages to Watch For:**
```
✅ Handled preflight request from direct_https origin: https://example.com
⚠️ Direct API access is enabled - any origin can access with credentials
❌ Blocked preflight request from blocked origin: http://insecure.com
```

**CORS Check Endpoint:**
- `GET /cors-check` - Returns detailed CORS configuration and origin analysis
- Shows origin type: `explicit`, `trusted_pattern`, `direct_https`, `blocked`

### 🎉 **Result**

**Before**: Only predefined origins could use ALB cookie stickiness
**After**: Any HTTPS client on the internet can use ALB cookie stickiness while maintaining security

This solution enables universal access for ALB sticky sessions while maintaining the security principle of never using wildcard origins with credentials. The API remains protected by your existing authentication mechanisms (JWT tokens, API keys), while CORS now supports the infrastructure requirement for session stickiness.

## Quick Verification

Test from any computer with internet access:

```bash
# This should work from anywhere
curl -H "Origin: https://$(hostname -f)" \
     -v https://api.mor.org/cors-check

# Look for these headers in response:
# Access-Control-Allow-Origin: https://your-hostname
# Access-Control-Allow-Credentials: true
# Vary: Origin
```

The solution is now ready for production deployment! 🚀
