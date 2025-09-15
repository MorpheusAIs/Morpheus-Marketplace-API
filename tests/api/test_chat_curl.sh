#!/bin/bash

# Test script for chat history API using curl
# Usage: ./test_chat_curl.sh sk-your-api-key-here

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <API_KEY>"
    echo "Example: $0 sk-TRuPTe..."
    exit 1
fi

API_KEY="$1"
BASE_URL="https://api.dev.mor.org/api/v1"

echo "🔍 Testing Chat History API with key: ${API_KEY:0:8}..."

echo ""
echo "1️⃣ Testing GET /chat-history/chats"
curl -H "Authorization: Bearer $API_KEY" \
     -H "Content-Type: application/json" \
     "$BASE_URL/chat-history/chats" \
     -w "\nStatus Code: %{http_code}\n" \
     -s

echo ""
echo ""
echo "2️⃣ Testing POST /chat-history/chats (create test chat)"
CHAT_RESPONSE=$(curl -H "Authorization: Bearer $API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"title": "Test Chat - Curl Debug"}' \
     "$BASE_URL/chat-history/chats" \
     -w "\nStatus Code: %{http_code}\n" \
     -s)

echo "$CHAT_RESPONSE"

# Extract chat ID if successful
CHAT_ID=$(echo "$CHAT_RESPONSE" | grep -o '"id":"[^"]*"' | cut -d'"' -f4)

if [ ! -z "$CHAT_ID" ]; then
    echo ""
    echo "3️⃣ Testing POST /chat-history/chats/$CHAT_ID/messages (add message)"
    curl -H "Authorization: Bearer $API_KEY" \
         -H "Content-Type: application/json" \
         -d '{"role": "user", "content": "Hello from curl test", "sequence": 1}' \
         "$BASE_URL/chat-history/chats/$CHAT_ID/messages" \
         -w "\nStatus Code: %{http_code}\n" \
         -s
    
    echo ""
    echo ""
    echo "4️⃣ Re-testing GET /chat-history/chats (should show new chat)"
    curl -H "Authorization: Bearer $API_KEY" \
         -H "Content-Type: application/json" \
         "$BASE_URL/chat-history/chats" \
         -w "\nStatus Code: %{http_code}\n" \
         -s
else
    echo "❌ Could not extract chat ID from response"
fi

echo ""
echo "✅ Test complete!"
