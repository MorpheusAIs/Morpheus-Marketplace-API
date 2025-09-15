# API URL Variablization Fix

## Problem Identified

The website had hardcoded API URLs that pointed to specific environments, preventing proper environment-based deployment.

### Issues Found:

1. **Hardcoded "newapi" URLs** in chat functionality
2. **Hardcoded documentation URLs** in multiple pages
3. **Inconsistent URL patterns** across the application

## 🔧 Solution Implemented

### ✅ **Fixed Chat Page URLs**

**Before:**
```typescript
// Hardcoded URLs with incorrect subdomain
const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL || 'https://newapi.dev.mor.org'}/api/v1/models`);
const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL || 'https://newapi.dev.mor.org'}/api/v1/chat/completions`);
```

**After:**
```typescript
// Uses centralized API configuration
const response = await fetch(API_URLS.models());
const res = await fetch(API_URLS.chatCompletions());
```

### ✅ **Enhanced API Configuration**

**Updated** `src/lib/api/config.ts`:
```typescript
// API Configuration (already existed)
export const API_CONFIG = {
  BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL || 'https://api.dev.mor.org',
  // ... rest of config
};

// NEW: Documentation URLs (environment-aware)
export const DOC_URLS = {
  swaggerUI: () => `${API_CONFIG.BASE_URL}/docs`,
  baseAPI: () => `${API_CONFIG.BASE_URL}${API_CONFIG.VERSION}`,
};
```

### ✅ **Updated Documentation Pages**

**Files Updated:**
- `src/app/docs/what-is-api-gateway/page.tsx`
- `src/app/docs/viewing-models/page.tsx`  
- `src/app/docs/using-swagger-ui/page.tsx`
- `src/app/docs/creating-api-key/page.tsx`

**Before:**
```typescript
// Hardcoded URLs
<a href="https://api.dev.mor.org/docs">https://api.dev.mor.org/docs</a>
<code>https://api.dev.mor.org/api/v1</code>
```

**After:**
```typescript
// Environment-aware URLs
import { DOC_URLS } from '@/lib/api/config';

<a href={DOC_URLS.swaggerUI()}>{DOC_URLS.swaggerUI()}</a>
<code>{DOC_URLS.baseAPI()}</code>
```

## 🌍 **Environment Configuration**

### **Development Environment:**
```bash
NEXT_PUBLIC_API_BASE_URL=https://api.dev.mor.org
```
**Results in:**
- API calls: `https://api.dev.mor.org/api/v1/*`
- Swagger UI: `https://api.dev.mor.org/docs`

### **Production Environment:**
```bash
NEXT_PUBLIC_API_BASE_URL=https://api.mor.org
```
**Results in:**
- API calls: `https://api.mor.org/api/v1/*`
- Swagger UI: `https://api.mor.org/docs`

### **Local Development:**
```bash
# If not set, defaults to dev environment
# NEXT_PUBLIC_API_BASE_URL=http://localhost:8000  # For local API
```

## 📊 **Files Modified**

### **API Configuration:**
- ✅ `src/lib/api/config.ts` - Added `DOC_URLS` helper

### **Chat Functionality:**
- ✅ `src/app/chat/page.tsx` - Fixed hardcoded URLs, use centralized config

### **Documentation Pages:**
- ✅ `src/app/docs/what-is-api-gateway/page.tsx` - Environment-aware URLs
- ✅ `src/app/docs/viewing-models/page.tsx` - Environment-aware URLs  
- ✅ `src/app/docs/using-swagger-ui/page.tsx` - Environment-aware URLs
- ✅ `src/app/docs/creating-api-key/page.tsx` - Environment-aware URLs

## 🧪 **Testing**

### **Verify Environment Variables Work:**

1. **Check development build:**
   ```bash
   npm run build
   # Should use https://api.dev.mor.org by default
   ```

2. **Check with custom environment:**
   ```bash
   NEXT_PUBLIC_API_BASE_URL=https://api.mor.org npm run build
   # Should use https://api.mor.org for production
   ```

3. **Verify URLs in browser:**
   - Documentation pages should show correct environment URLs
   - API calls should go to correct environment

## ✅ **Benefits**

### **1. Environment Consistency:**
- All API URLs automatically match the deployment environment
- No more hardcoded environment-specific URLs

### **2. Easy Deployment:**
- Same codebase works for dev, staging, and production
- Just set `NEXT_PUBLIC_API_BASE_URL` environment variable

### **3. Maintainability:**
- Centralized URL configuration
- Single source of truth for API endpoints

### **4. Documentation Accuracy:**
- Documentation pages show URLs for current environment
- Users see correct URLs for their deployment

## 🚀 **Deployment Notes**

### **Environment Variables Required:**

**Development:**
```bash
NEXT_PUBLIC_API_BASE_URL=https://api.dev.mor.org
```

**Production:**
```bash  
NEXT_PUBLIC_API_BASE_URL=https://api.mor.org
```

### **Verification Steps:**
1. Deploy to dev environment
2. Check that documentation shows `api.dev.mor.org` URLs
3. Verify API calls go to dev environment
4. Test chat functionality with dev API
5. Deploy to production with production environment variable
6. Verify production URLs are used

## 📋 **Summary**

**Fixed Issues:**
- ❌ `https://newapi.dev.mor.org` → ✅ Environment-aware URLs
- ❌ Hardcoded documentation URLs → ✅ Dynamic URLs
- ❌ Inconsistent URL patterns → ✅ Centralized configuration

**All API URLs are now properly variablized and will automatically use the correct environment based on the `NEXT_PUBLIC_API_BASE_URL` environment variable.**
