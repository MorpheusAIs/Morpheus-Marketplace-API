#!/usr/bin/env python3
"""
End-to-End test script for the Morpheus API Automation feature.
This simulates the full user flow from enabling automation to making a chat completions request.
"""

import requests
import json
import os
import sys
import time
from pathlib import Path

# API settings - replace with your actual API endpoint and key
API_BASE_URL = os.environ.get("MORPHEUS_API_URL", "http://localhost:8000")
API_KEY = os.environ.get("MORPHEUS_API_KEY", "")

# Headers for API requests
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}"
}

def print_step(message):
    """Print a step message with formatting."""
    print(f"\n=== {message} ===")

def check_api_connection():
    """Check if we can connect to the API."""
    print_step("Checking API connection")
    
    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            print("✅ API is accessible")
            return True
        else:
            print(f"❌ API returned status code {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Failed to connect to API: {str(e)}")
        return False

def get_current_automation_settings():
    """Get the current automation settings for the user."""
    print_step("Getting current automation settings")
    
    try:
        response = requests.get(
            f"{API_BASE_URL}/api/v1/automation/settings",
            headers=headers,
            timeout=5
        )
        
        if response.status_code == 200:
            settings = response.json()
            print(f"✅ Got automation settings: {json.dumps(settings, indent=2)}")
            return settings
        elif response.status_code == 404:
            print("ℹ️ Automation endpoint not found - feature may be disabled")
            return None
        else:
            print(f"❌ Failed to get settings: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return None

def enable_automation():
    """Enable automation for the user."""
    print_step("Enabling automation")
    
    data = {
        "is_enabled": True,
        "session_duration": 3600  # 1 hour
    }
    
    try:
        response = requests.put(
            f"{API_BASE_URL}/api/v1/automation/settings",
            headers=headers,
            json=data,
            timeout=5
        )
        
        if response.status_code == 200:
            settings = response.json()
            print(f"✅ Automation enabled: {json.dumps(settings, indent=2)}")
            return settings
        else:
            print(f"❌ Failed to enable automation: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return None

def check_active_session():
    """Check if there is an active session."""
    print_step("Checking for active session")
    
    try:
        response = requests.get(
            f"{API_BASE_URL}/api/v1/session/status",
            headers=headers,
            timeout=5
        )
        
        if response.status_code == 200:
            status = response.json()
            print(f"✅ Session status: {json.dumps(status, indent=2)}")
            return status.get("active", False)
        else:
            print(f"❌ Failed to get session status: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def make_chat_completion_request():
    """Make a chat completion request that should trigger automated session creation."""
    print_step("Making chat completion request")
    
    data = {
        "model": "gpt-4",
        "messages": [
            {"role": "user", "content": "Hello, this is a test of the automation feature."}
        ],
        "max_tokens": 50
    }
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30  # longer timeout for completions
        )
        
        if response.status_code == 200:
            completion = response.json()
            content = completion.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"✅ Got completion response: {content}")
            return True
        else:
            print(f"❌ Failed to get completion: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def run_e2e_test():
    """Run the full end-to-end test."""
    print("\n🚀 Starting Automation E2E Test")
    
    # Check if API key is provided
    if not API_KEY:
        print("❌ ERROR: No API key provided. Set MORPHEUS_API_KEY environment variable.")
        return False
    
    # Check API connection
    if not check_api_connection():
        return False
    
    # Get current settings
    initial_settings = get_current_automation_settings()
    
    # Enable automation if endpoint exists
    if initial_settings is not None:
        updated_settings = enable_automation()
        if not updated_settings:
            return False
    else:
        print("ℹ️ Skipping automation settings update - endpoint not available")
    
    # Check if there's an active session before chat request
    initial_session_active = check_active_session()
    
    # Make a chat completion request
    if not make_chat_completion_request():
        return False
    
    # Check if a session was created (if there wasn't one already)
    if not initial_session_active:
        time.sleep(1)  # Give it a moment to register the session
        final_session_active = check_active_session()
        
        if final_session_active:
            print("✅ Session was automatically created!")
        else:
            print("❌ No session was created - automation may not be working")
            return False
    else:
        print("ℹ️ Session was already active - couldn't verify automatic creation")
    
    print("\n✅ End-to-End test completed successfully!")
    return True

if __name__ == "__main__":
    success = run_e2e_test()
    sys.exit(0 if success else 1) 