#!/usr/bin/env python3
"""
Test script to verify chat storage functionality and authentication consistency.
"""
import requests
import json
import os
from datetime import datetime

# Configuration
API_BASE_URL = "https://api.dev.mor.org"
API_VERSION = "/api/v1"

# Test data
TEST_API_KEY = os.getenv("TEST_API_KEY", "sk-test-key-here")
TEST_JWT_TOKEN = os.getenv("TEST_JWT_TOKEN", "jwt-token-here")

def test_endpoint(url, method="GET", headers=None, data=None, description=""):
    """Test an API endpoint and return results."""
    print(f"\n🔍 Testing: {description}")
    print(f"   {method} {url}")
    
    try:
        if method == "GET":
            response = requests.get(url, headers=headers)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=data)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers)
        else:
            print(f"   ❌ Unsupported method: {method}")
            return None
            
        print(f"   Status: {response.status_code}")
        
        if response.status_code < 400:
            try:
                result = response.json()
                print(f"   ✅ Success: {json.dumps(result, indent=2)[:200]}...")
                return result
            except:
                print(f"   ✅ Success: {response.text[:200]}...")
                return response.text
        else:
            try:
                error = response.json()
                print(f"   ❌ Error: {json.dumps(error, indent=2)}")
            except:
                print(f"   ❌ Error: {response.text}")
            return None
            
    except Exception as e:
        print(f"   ❌ Exception: {str(e)}")
        return None

def main():
    """Run chat storage tests."""
    print("=" * 60)
    print("🧪 CHAT STORAGE FUNCTIONALITY TEST")
    print("=" * 60)
    
    # Test 1: Health check
    health_result = test_endpoint(
        f"{API_BASE_URL}/health",
        description="API Health Check"
    )
    
    if not health_result:
        print("❌ API is not healthy, stopping tests")
        return
    
    print(f"\n📊 API Status:")
    print(f"   Version: {health_result.get('version', 'unknown')}")
    print(f"   Database: {health_result.get('database', 'unknown')}")
    print(f"   Uptime: {health_result.get('uptime', {}).get('human_readable', 'unknown')}")
    
    # Test 2: Chat History with JWT Token
    print(f"\n" + "=" * 60)
    print("🔐 TESTING JWT AUTHENTICATION")
    print("=" * 60)
    
    jwt_headers = {
        "Authorization": f"Bearer {TEST_JWT_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # List chats with JWT
    chats_jwt = test_endpoint(
        f"{API_BASE_URL}{API_VERSION}/chat-history/chats",
        headers=jwt_headers,
        description="List chats with JWT token"
    )
    
    # Create chat with JWT
    new_chat_jwt = test_endpoint(
        f"{API_BASE_URL}{API_VERSION}/chat-history/chats",
        method="POST",
        headers=jwt_headers,
        data={"title": f"Test Chat JWT {datetime.now().strftime('%H:%M:%S')}"},
        description="Create chat with JWT token"
    )
    
    # Test 3: Chat History with API Key
    print(f"\n" + "=" * 60)
    print("🔑 TESTING API KEY AUTHENTICATION")
    print("=" * 60)
    
    api_key_headers = {
        "Authorization": f"Bearer {TEST_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # List chats with API key
    chats_api_key = test_endpoint(
        f"{API_BASE_URL}{API_VERSION}/chat-history/chats",
        headers=api_key_headers,
        description="List chats with API key"
    )
    
    # Create chat with API key
    new_chat_api_key = test_endpoint(
        f"{API_BASE_URL}{API_VERSION}/chat-history/chats",
        method="POST",
        headers=api_key_headers,
        data={"title": f"Test Chat API Key {datetime.now().strftime('%H:%M:%S')}"},
        description="Create chat with API key"
    )
    
    # Test 4: Chat Completions (should use API key)
    print(f"\n" + "=" * 60)
    print("💬 TESTING CHAT COMPLETIONS")
    print("=" * 60)
    
    completion_data = {
        "model": "mistral-31-24b",
        "messages": [
            {"role": "user", "content": "Hello, this is a test message"}
        ],
        "stream": False
    }
    
    completion_result = test_endpoint(
        f"{API_BASE_URL}{API_VERSION}/chat/completions",
        method="POST",
        headers=api_key_headers,
        data=completion_data,
        description="Chat completion with API key"
    )
    
    # Test 5: Message operations if we have a chat
    if new_chat_jwt and 'id' in new_chat_jwt:
        chat_id = new_chat_jwt['id']
        print(f"\n" + "=" * 60)
        print(f"📝 TESTING MESSAGE OPERATIONS (Chat ID: {chat_id})")
        print("=" * 60)
        
        # Add message to chat
        message_result = test_endpoint(
            f"{API_BASE_URL}{API_VERSION}/chat-history/chats/{chat_id}/messages",
            method="POST",
            headers=jwt_headers,
            data={
                "role": "user",
                "content": "Test message content",
                "tokens": 10
            },
            description="Add message to chat with JWT"
        )
        
        # Get chat messages
        messages_result = test_endpoint(
            f"{API_BASE_URL}{API_VERSION}/chat-history/chats/{chat_id}/messages",
            headers=jwt_headers,
            description="Get chat messages with JWT"
        )
        
        # Get full chat details
        chat_detail_result = test_endpoint(
            f"{API_BASE_URL}{API_VERSION}/chat-history/chats/{chat_id}",
            headers=jwt_headers,
            description="Get chat details with JWT"
        )
    
    # Summary
    print(f"\n" + "=" * 60)
    print("📋 TEST SUMMARY")
    print("=" * 60)
    
    print(f"✅ API Health: {'OK' if health_result else 'FAILED'}")
    print(f"🔐 JWT Chat List: {'OK' if chats_jwt is not None else 'FAILED'}")
    print(f"🔐 JWT Chat Create: {'OK' if new_chat_jwt else 'FAILED'}")
    print(f"🔑 API Key Chat List: {'OK' if chats_api_key is not None else 'FAILED'}")
    print(f"🔑 API Key Chat Create: {'OK' if new_chat_api_key else 'FAILED'}")
    print(f"💬 Chat Completions: {'OK' if completion_result else 'FAILED'}")
    
    print(f"\n🎯 AUTHENTICATION CONSISTENCY ANALYSIS:")
    if chats_jwt is not None and chats_api_key is not None:
        jwt_count = len(chats_jwt) if isinstance(chats_jwt, list) else len(chats_jwt.get('data', []))
        api_key_count = len(chats_api_key) if isinstance(chats_api_key, list) else len(chats_api_key.get('data', []))
        
        print(f"   JWT shows {jwt_count} chats")
        print(f"   API Key shows {api_key_count} chats")
        
        if jwt_count != api_key_count:
            print("   ⚠️  INCONSISTENT: Different chat counts between JWT and API key!")
        else:
            print("   ✅ CONSISTENT: Same chat count for both auth methods")
    else:
        print("   ❌ Cannot compare - one or both auth methods failed")

if __name__ == "__main__":
    print("Please set environment variables:")
    print("export TEST_API_KEY='your-api-key-here'")
    print("export TEST_JWT_TOKEN='your-jwt-token-here'")
    print()
    
    if TEST_API_KEY == "sk-test-key-here" or TEST_JWT_TOKEN == "jwt-token-here":
        print("⚠️  Using default test values - set real tokens for accurate testing")
        print()
    
    main()
