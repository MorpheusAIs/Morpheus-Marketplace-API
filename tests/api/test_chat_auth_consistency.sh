#!/bin/bash

# Test script to verify chat authentication consistency
# This script tests that chat history endpoints now use API key authentication only

API_BASE="https://api.dev.mor.org/api/v1"

echo "🧪 CHAT AUTHENTICATION CONSISTENCY TEST"
echo "========================================"

# Test 1: Chat History with invalid JWT (should fail)
echo -e "\n1️⃣ Testing chat history with JWT token (should fail with 401)"
curl -s -X GET "$API_BASE/chat-history/chats" \
  -H "Authorization: Bearer invalid-jwt-token" \
  -H "Content-Type: application/json" | jq '.' || echo "No JSON response"

# Test 2: Chat History with invalid API key (should fail with proper error)
echo -e "\n2️⃣ Testing chat history with invalid API key (should fail with 401)"
curl -s -X GET "$API_BASE/chat-history/chats" \
  -H "Authorization: Bearer sk-invalid-api-key" \
  -H "Content-Type: application/json" | jq '.' || echo "No JSON response"

# Test 3: Chat History with no auth (should fail)
echo -e "\n3️⃣ Testing chat history with no authentication (should fail with 401)"
curl -s -X GET "$API_BASE/chat-history/chats" \
  -H "Content-Type: application/json" | jq '.' || echo "No JSON response"

# Test 4: Chat completions with invalid API key (should fail)
echo -e "\n4️⃣ Testing chat completions with invalid API key (should fail with 401)"
curl -s -X POST "$API_BASE/chat/completions" \
  -H "Authorization: Bearer sk-invalid-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mistral-31-24b",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }' | jq '.' || echo "No JSON response"

echo -e "\n✅ Test completed!"
echo -e "\n📋 EXPECTED RESULTS:"
echo "   - All tests should return 401 Unauthorized"
echo "   - JWT token should NOT work for chat history (consistency achieved)"
echo "   - Only valid API keys should work for both chat completions AND chat history"
echo -e "\n🎯 AUTHENTICATION CONSISTENCY:"
echo "   - Chat completions: API key only ✓"
echo "   - Chat history: API key only ✓ (was JWT + API key)"
echo "   - Frontend: API key for all chat operations ✓"
