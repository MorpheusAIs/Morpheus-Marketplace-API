#!/usr/bin/env python3
"""
Single Request Latency Tracer
Sends one request and traces it through the entire system stack
"""

import requests
import json
import time
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Configuration
API_URL = "https://api.stg.mor.org"
AWS_PROFILE = "mor-org-prd"
AWS_REGION = "us-east-2"
API_LOG_GROUP = "/aws/ecs/services/stg/morpheus-api"
CNODE_LOG_GROUP = "/aws/ecs/services/dev/morpheus-router"


def get_api_key() -> str:
    """Get API key from environment or prompt"""
    import os
    api_key = os.getenv("API_KEY")
    if not api_key:
        print("Error: API_KEY environment variable must be set")
        sys.exit(1)
    return api_key


def send_request(api_key: str, model: str = "mistral-31-24b") -> Dict:
    """Send a single chat completion request and track timing"""
    request_id = str(uuid.uuid4())[:8]
    
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": "Hello, respond with exactly 5 words."
            }
        ],
        "stream": False,
        "max_tokens": 20
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-Request-ID": request_id
    }
    
    print("=" * 60)
    print("SINGLE REQUEST LATENCY TRACE")
    print("=" * 60)
    print(f"Request ID: {request_id}")
    print(f"Model: {model}")
    print(f"Start Time: {datetime.now(timezone.utc).isoformat()}")
    print()
    
    # Track timing
    time_start = time.time()
    timestamp_start_ms = int(time_start * 1000)
    
    print("Sending request...")
    
    try:
        response = requests.post(
            f"{API_URL}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=180
        )
        
        time_end = time.time()
        timestamp_end_ms = int(time_end * 1000)
        total_latency_ms = int((time_end - time_start) * 1000)
        
        print(f"✓ Response received in {total_latency_ms}ms")
        print()
        
        result = {
            "request_id": request_id,
            "timestamp_start_ms": timestamp_start_ms,
            "timestamp_end_ms": timestamp_end_ms,
            "total_latency_ms": total_latency_ms,
            "status_code": response.status_code,
            "response": response.json() if response.ok else response.text
        }
        
        # Display response summary
        print("=" * 60)
        print("RESPONSE SUMMARY")
        print("=" * 60)
        print(f"HTTP Status: {response.status_code}")
        print(f"Total Latency: {total_latency_ms}ms ({total_latency_ms/1000:.2f}s)")
        
        if response.ok:
            data = response.json()
            print(f"Model: {data.get('model', 'N/A')}")
            if 'usage' in data:
                usage = data['usage']
                print(f"Tokens - Input: {usage.get('prompt_tokens', 'N/A')}, "
                      f"Output: {usage.get('completion_tokens', 'N/A')}, "
                      f"Total: {usage.get('total_tokens', 'N/A')}")
            
            if 'choices' in data and data['choices']:
                content = data['choices'][0].get('message', {}).get('content', '')
                print(f"Response: {content[:100]}...")
        else:
            print(f"Error: {response.text[:200]}")
        
        print()
        return result
        
    except Exception as e:
        print(f"✗ Request failed: {e}")
        return {
            "request_id": request_id,
            "timestamp_start_ms": timestamp_start_ms,
            "error": str(e)
        }


def fetch_logs(log_group: str, request_id: str, start_ms: int, end_ms: int) -> List[Dict]:
    """Fetch logs from CloudWatch"""
    # Add 5 second buffer
    start_ms -= 5000
    end_ms += 5000
    
    cmd = [
        "aws", "logs", "filter-log-events",
        "--log-group-name", log_group,
        "--profile", AWS_PROFILE,
        "--region", AWS_REGION,
        "--start-time", str(start_ms),
        "--end-time", str(end_ms),
        "--output", "json"
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            events = data.get("events", [])
            
            # Filter for our request ID
            relevant_events = []
            for event in events:
                message = event.get("message", "")
                if request_id in message:
                    try:
                        # Try to parse as JSON
                        log_data = json.loads(message)
                        log_data["_raw_timestamp"] = event.get("timestamp")
                        relevant_events.append(log_data)
                    except:
                        # Keep as string if not JSON
                        relevant_events.append({
                            "_raw_message": message,
                            "_raw_timestamp": event.get("timestamp")
                        })
            
            return relevant_events
        else:
            print(f"Warning: Failed to fetch logs from {log_group}: {result.stderr}")
            return []
    except Exception as e:
        print(f"Warning: Error fetching logs: {e}")
        return []


def analyze_api_logs(logs: List[Dict]) -> None:
    """Analyze API gateway logs to extract timing"""
    print("=" * 60)
    print("API GATEWAY TRACE")
    print("=" * 60)
    
    if not logs:
        print("No logs found")
        return
    
    # Sort by timestamp
    logs = sorted(logs, key=lambda x: x.get("_raw_timestamp", 0))
    
    # Key events to track
    events = {
        "request_received": None,
        "rate_limit_check": None,
        "session_selected": None,
        "proxy_request_start": None,
        "proxy_response_received": None,
        "response_sent": None
    }
    
    for log in logs:
        event_type = log.get("event_type", "")
        timestamp_str = log.get("timestamp", "")
        
        print(f"  [{timestamp_str}] {event_type or log.get('event', 'unknown')}")
        
        # Track key events
        if "chat_request_processing" in event_type:
            events["request_received"] = timestamp_str
        elif "rate_limit_check_passed" in event_type:
            events["rate_limit_check"] = timestamp_str
            rpm = log.get("rpm_remaining", "?")
            print(f"    → RPM remaining: {rpm}")
        elif "session_selected" in event_type or "session_found" in event_type:
            events["session_selected"] = timestamp_str
            session_id = log.get("session_id", "?")
            print(f"    → Session: {session_id}")
        elif "proxy_request" in event_type or "forwarding" in event_type:
            events["proxy_request_start"] = timestamp_str
        elif "proxy_response" in event_type or "received" in event_type:
            events["proxy_response_received"] = timestamp_str
        elif "response_sent" in event_type or "completed" in event_type:
            events["response_sent"] = timestamp_str
            duration = log.get("duration_ms", log.get("duration", "?"))
            print(f"    → Duration: {duration}ms")
    
    print()
    print("Timeline:")
    for event_name, timestamp in events.items():
        if timestamp:
            print(f"  {event_name}: {timestamp}")
    print()


def analyze_cnode_logs(logs: List[Dict]) -> None:
    """Analyze C-Node logs to extract timing"""
    print("=" * 60)
    print("C-NODE (ROUTER) TRACE")
    print("=" * 60)
    
    if not logs:
        print("No logs found (request may not have reached c-node yet)")
        return
    
    # Sort by timestamp
    logs = sorted(logs, key=lambda x: x.get("_raw_timestamp", 0))
    
    for log in logs:
        if "_raw_message" in log:
            print(f"  {log['_raw_message']}")
        else:
            timestamp_str = log.get("timestamp", "")
            event = log.get("event", log.get("message", ""))
            print(f"  [{timestamp_str}] {event}")
    
    print()


def main():
    """Main execution"""
    api_key = get_api_key()
    model = sys.argv[1] if len(sys.argv) > 1 else "mistral-31-24b"
    
    # Send request
    result = send_request(api_key, model)
    
    if "error" in result:
        print("Request failed, skipping log analysis")
        return
    
    request_id = result["request_id"]
    start_ms = result["timestamp_start_ms"]
    end_ms = result["timestamp_end_ms"]
    
    # Wait a moment for logs to propagate
    print("Waiting 3 seconds for logs to propagate...")
    time.sleep(3)
    print()
    
    # Fetch and analyze API logs
    print("Fetching API gateway logs...")
    api_logs = fetch_logs(API_LOG_GROUP, request_id, start_ms, end_ms)
    analyze_api_logs(api_logs)
    
    # Fetch and analyze C-Node logs
    print("Fetching C-Node logs...")
    cnode_logs = fetch_logs(CNODE_LOG_GROUP, request_id, start_ms, end_ms)
    analyze_cnode_logs(cnode_logs)
    
    # Summary
    print("=" * 60)
    print("LATENCY BREAKDOWN SUMMARY")
    print("=" * 60)
    print(f"Total End-to-End: {result['total_latency_ms']}ms")
    print()
    print("Components:")
    print("  1. Client → API Gateway (network)")
    print("  2. API Gateway (rate limit, DB, session routing)")
    print("  3. API → C-Node (network + routing)")
    print("  4. C-Node → Provider (session establishment)")
    print("  5. Provider → LLM (inference)")
    print("  6. Response path back")
    print()
    print(f"Found {len(api_logs)} API log entries")
    print(f"Found {len(cnode_logs)} C-Node log entries")
    print()


if __name__ == "__main__":
    main()
