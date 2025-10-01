#!/usr/bin/env python3
"""
Test script for chat history API endpoints
"""
import requests
import json
import sys

def test_chat_api(api_key):
    base_url = "https://api.dev.mor.org/api/v1"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    print("🔍 Testing Chat History API...")
    print(f"Using API key: {api_key[:8]}...")
    
    # Test 1: Get existing chats
    print("\n1️⃣ Testing GET /chat-history/chats")
    try:
        response = requests.get(f"{base_url}/chat-history/chats", headers=headers)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            chats = response.json()
            print(f"Found {len(chats)} chats:")
            for chat in chats:
                print(f"  - Chat ID: {chat.get('id')}, Title: {chat.get('title')}")
        else:
            print(f"Error: {response.text}")
    except Exception as e:
        print(f"Request failed: {e}")
    
    # Test 2: Create a test chat
    print("\n2️⃣ Testing POST /chat-history/chats")
    test_chat_data = {
        "title": "Test Chat - API Debug"
    }
    try:
        response = requests.post(f"{base_url}/chat-history/chats", 
                               headers=headers, 
                               json=test_chat_data)
        print(f"Status: {response.status_code}")
        if response.status_code == 201:
            chat = response.json()
            chat_id = chat.get('id')
            print(f"Created chat: {chat_id}")
            
            # Test 3: Add a message to the chat
            print(f"\n3️⃣ Testing POST /chat-history/chats/{chat_id}/messages")
            message_data = {
                "role": "user",
                "content": "Hello, this is a test message",
                "sequence": 1
            }
            msg_response = requests.post(f"{base_url}/chat-history/chats/{chat_id}/messages",
                                       headers=headers,
                                       json=message_data)
            print(f"Message Status: {msg_response.status_code}")
            if msg_response.status_code == 201:
                message = msg_response.json()
                print(f"Created message: {message.get('id')}")
            else:
                print(f"Message Error: {msg_response.text}")
                
        else:
            print(f"Error: {response.text}")
    except Exception as e:
        print(f"Request failed: {e}")
    
    # Test 4: Check chats again
    print("\n4️⃣ Re-testing GET /chat-history/chats")
    try:
        response = requests.get(f"{base_url}/chat-history/chats", headers=headers)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            chats = response.json()
            print(f"Now found {len(chats)} chats:")
            for chat in chats:
                print(f"  - Chat ID: {chat.get('id')}, Title: {chat.get('title')}")
        else:
            print(f"Error: {response.text}")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python test_chat_api.py <API_KEY>")
        print("Example: python test_chat_api.py sk-TRuPTe...")
        sys.exit(1)
    
    api_key = sys.argv[1]
    test_chat_api(api_key)
