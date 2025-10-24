# Get Token Endpoint Setup Guide

## Overview

A new user-friendly `/get-token` endpoint has been added to the API Gateway that allows users to obtain their JWT tokens directly through a web interface.

## How It Works

1. User navigates to the endpoint (or uses the direct Cognito URL)
2. Redirected to Cognito Hosted UI for authentication
3. After successful login, tokens are displayed on a beautiful web page
4. Users can copy tokens with one click

## User-Facing URLs

### Production
```
https://api.mor.org/get-token
```

**Direct Cognito Auth URL:**
```
https://auth.mor.org/oauth2/authorize?client_id=7faqqo5lcj3175epjqs2upvmmu&response_type=code&scope=openid+email+profile&redirect_uri=https://api.mor.org/get-token
```

### Development
```
https://api.dev.mor.org/get-token
```

**Direct Cognito Auth URL:**
```
https://auth.mor.org/oauth2/authorize?client_id=7faqqo5lcj3175epjqs2upvmmu&response_type=code&scope=openid+email+profile&redirect_uri=https://api.dev.mor.org/get-token
```

## Required Cognito Configuration

To enable this endpoint, the following callback URLs need to be added to the Cognito User Pool App Client configuration.

### Callback URLs to Add

Add these to the **Allowed callback URLs** in the Cognito App Client settings:

```
https://api.mor.org/get-token
https://api.dev.mor.org/get-token
http://localhost:8000/get-token
```

### How to Configure in AWS Console

1. Go to **AWS Console** → **Cognito** → **User Pools**
2. Select User Pool: `us-east-2_tqCTHoSST`
3. Navigate to **App Integration** → **App clients and analytics**
4. Click on App Client: `7faqqo5lcj3175epjqs2upvmmu`
5. Click **Edit** under **Hosted UI**
6. Add the new callback URLs to the **Allowed callback URLs** list:
   - `https://api.mor.org/get-token`
   - `https://api.dev.mor.org/get-token`
   - `http://localhost:8000/get-token`
7. Click **Save changes**

### Terraform Configuration (If Using Infrastructure as Code)

If the Cognito configuration is managed by Terraform, add these callback URLs to the `callback_urls` list in the `aws_cognito_user_pool_client` resource:

```hcl
resource "aws_cognito_user_pool_client" "api" {
  # ... existing configuration ...
  
  # Callback URLs for your API and Frontend
  callback_urls = [
    # Existing callbacks
    "${local.api_base_url}/docs/oauth2-redirect",
    "${local.api_base_url}/docs/",
    "http://localhost:8000/docs/oauth2-redirect",
    "http://localhost:8000/docs/",
    
    # NEW: Get Token endpoint callbacks
    "${local.api_base_url}/get-token",
    "http://localhost:8000/get-token",
    
    # Frontend callback URLs (existing)
    var.env_lifecycle == "prd" ? "https://openbeta.mor.org/auth/callback" : "https://openbeta.${var.env_lifecycle}.mor.org/auth/callback",
    var.env_lifecycle == "prd" ? "https://openbeta.mor.org/" : "https://openbeta.${var.env_lifecycle}.mor.org/",
    var.env_lifecycle == "prd" ? "https://openbeta.mor.org/signup" : "https://openbeta.${var.env_lifecycle}.mor.org/signup"
  ]
  
  # ... rest of configuration ...
}
```

**Note:** The `${local.api_base_url}` variable should resolve to:
- `https://api.mor.org` for production
- `https://api.dev.mor.org` for development

## Features

### Landing Page
- Clean, user-friendly interface
- Clear instructions on how to get tokens
- One-click "Login with Cognito" button
- Direct authentication URL provided

### Token Display Page
- Shows user information (name, email)
- Displays all three tokens:
  - **ID Token (JWT)** - Primary token for API authentication
  - **Access Token** - For Cognito user pool access
  - **Refresh Token** - For obtaining new tokens (30-day validity)
- One-click copy buttons for each token
- Usage examples with curl commands
- Tokens also logged to browser console for easy access
- Beautiful, responsive design with gradient background

### Error Handling
- Graceful handling of OAuth errors
- Clear error messages if token exchange fails
- User-friendly error pages with navigation back to home

## Testing

### After Configuration

Once the callback URLs are added to Cognito, test the endpoint:

1. **Direct Browser Test:**
   - Navigate to: `https://api.mor.org/get-token`
   - Should see landing page with login button
   - Click "Login with Cognito"
   - Should redirect to Cognito login
   - After login, should see tokens displayed

2. **Direct Auth URL Test:**
   - Navigate to: `https://auth.mor.org/oauth2/authorize?client_id=7faqqo5lcj3175epjqs2upvmmu&response_type=code&scope=openid+email+profile&redirect_uri=https://api.mor.org/get-token`
   - Should redirect to Cognito login immediately
   - After login, should see tokens displayed

3. **Local Development Test:**
   - Navigate to: `http://localhost:8000/get-token`
   - Same flow should work for local testing

### Expected User Flow

```
User visits /get-token
    ↓
Landing page with "Login" button
    ↓
User clicks "Login with Cognito"
    ↓
Redirected to auth.mor.org (Cognito Hosted UI)
    ↓
User signs in or signs up
    ↓
Redirected back to /get-token?code=...
    ↓
Backend exchanges code for tokens
    ↓
Beautiful page displays all tokens with copy buttons
    ↓
User copies ID Token and uses it for API calls
```

## Security Considerations

- Uses OAuth 2.0 Authorization Code flow (most secure)
- Tokens are only displayed to the authenticated user
- HTTPS required for production endpoints
- Tokens expire after 60 minutes (configurable in Cognito)
- Refresh tokens valid for 30 days

## User Documentation

### How to Get Your JWT Token

1. **Visit the Get Token page:**
   - Production: https://api.mor.org/get-token
   - Development: https://api.dev.mor.org/get-token

2. **Click "Login with Cognito"**

3. **Sign in with your Morpheus account** (or sign up if you don't have one)

4. **Your tokens will be displayed:**
   - Copy the **ID Token (JWT)** - this is what you need
   - Access Token and Refresh Token are also provided

5. **Use your ID Token in API calls:**
   ```bash
   curl -X GET "https://api.mor.org/api/v1/models" \
     -H "Authorization: Bearer YOUR_ID_TOKEN_HERE"
   ```

## Deployment Checklist

- [x] Add `/get-token` endpoint to `src/main.py`
- [ ] Add callback URLs to Cognito App Client configuration
- [ ] Test in development environment
- [ ] Test in production environment
- [ ] Update user documentation with the new URL
- [ ] Communicate new feature to users

## Support

If users encounter issues:

1. **Verify Cognito Configuration:** Ensure callback URLs are correctly configured
2. **Check Browser Console:** Tokens are logged for debugging
3. **Verify Environment:** Ensure using correct environment URLs (dev vs prod)
4. **Token Expiry:** Tokens expire after 60 minutes - get a new one if needed

## Related Endpoints

- `/docs/oauth2-redirect` - OAuth redirect for Swagger UI
- `/exchange-token` - Programmatic token exchange (returns JSON)
- `/docs` - Swagger UI with OAuth integration

## Implementation Details

**File:** `src/main.py` (lines ~1273-1659)

The endpoint handles three scenarios:
1. **No parameters** - Shows landing page with login button
2. **OAuth error** - Displays error page with details
3. **Authorization code** - Exchanges code for tokens and displays them

The endpoint is excluded from the OpenAPI schema (`include_in_schema=False`) as it's a user-facing web page, not an API endpoint.


