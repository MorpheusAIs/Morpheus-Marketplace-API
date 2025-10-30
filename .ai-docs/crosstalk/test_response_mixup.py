#!/usr/bin/env python3
"""
Test for Response Routing Mixup

Tests if responses get delivered to wrong clients.
This is a CRITICAL security test.

Usage:
    python test_response_mixup.py --api-key1 <KEY1> --api-key2 <KEY2>
"""

import asyncio
import argparse
import httpx
import time
from typing import List, Dict


async def test_cross_key_mixup(
    base_url: str,
    api_key1: str,
    api_key2: str,
    model: str = "qwen3-235b"
):
    """
    Test if responses get mixed between different API keys.
    
    This is a CRITICAL security test!
    """
    print("╔" + "═" * 78 + "╗")
    print("║" + " " * 20 + "🚨 RESPONSE MIXUP TEST (CRITICAL) 🚨" + " " * 20 + "║")
    print("╚" + "═" * 78 + "╝")
    print()
    print("Testing if responses get delivered to wrong API keys...")
    print("─" * 80)
    
    url = f"{base_url}/v1/chat/completions"
    
    # Send unique prompts from each API key
    async with httpx.AsyncClient(timeout=60.0) as client:
        # API Key 1 sends distinctive prompt
        task1 = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key1}"},
            json={
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": "Say only these words: APPLE BANANA CHERRY. Nothing else."
                }],
                "temperature": 0.1,
                "max_tokens": 20
            }
        )
        
        # API Key 2 sends different distinctive prompt (at same time)
        task2 = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key2}"},
            json={
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": "Say only these words: XRAY YANKEE ZULU. Nothing else."
                }],
                "temperature": 0.1,
                "max_tokens": 20
            }
        )
        
        print(f"Sending concurrent requests:")
        print(f"  API Key 1 → Expecting: APPLE BANANA CHERRY")
        print(f"  API Key 2 → Expecting: XRAY YANKEE ZULU")
        print()
        
        start = time.time()
        results = await asyncio.gather(task1, task2, return_exceptions=True)
        duration = time.time() - start
        
        print(f"Completed in {duration:.1f}s")
        print("─" * 80)
        
        # Analyze responses
        mixup_detected = False
        
        for i, (result, expected, api_key) in enumerate([
            (results[0], "APPLE BANANA CHERRY", api_key1),
            (results[1], "XRAY YANKEE ZULU", api_key2)
        ], 1):
            print(f"\nAPI Key {i} ({api_key[-8:]}):")
            
            if isinstance(result, Exception):
                print(f"  ❌ Error: {result}")
                continue
            
            if result.status_code != 200:
                print(f"  ❌ HTTP {result.status_code}: {result.text[:100]}")
                continue
            
            try:
                body = result.json()
                content = body["choices"][0]["message"]["content"]
                
                print(f"  Expected: {expected}")
                print(f"  Received: {content}")
                
                # Check for mixup
                if i == 1 and ("XRAY" in content or "YANKEE" in content or "ZULU" in content):
                    print(f"  🚨🚨🚨 CRITICAL BUG: API Key 1 got API Key 2's response!")
                    mixup_detected = True
                elif i == 2 and ("APPLE" in content or "BANANA" in content or "CHERRY" in content):
                    print(f"  🚨🚨🚨 CRITICAL BUG: API Key 2 got API Key 1's response!")
                    mixup_detected = True
                else:
                    print(f"  ✅ Correct response")
                    
            except Exception as e:
                print(f"  ❌ Parse error: {e}")
        
        print()
        print("─" * 80)
        
        if mixup_detected:
            print("🚨🚨🚨 SECURITY VIOLATION DETECTED! 🚨🚨🚨")
            print("Responses are being delivered to WRONG API keys!")
            print("This is a data leak and privacy violation!")
            print("FIX IMMEDIATELY before production use!")
        else:
            print("✅ No cross-key mixup detected (but test same-key next)")
        
        print("─" * 80)
        
        return mixup_detected


async def test_same_key_concurrent_mixup(
    base_url: str,
    api_key: str,
    concurrency: int = 5,
    model: str = "qwen3-235b"
):
    """
    Test if concurrent requests from SAME API key get mixed responses.
    """
    print()
    print("╔" + "═" * 78 + "╗")
    print("║" + " " * 18 + "SAME API KEY CONCURRENT REQUEST TEST" + " " * 22 + "║")
    print("╚" + "═" * 78 + "╝")
    print()
    print(f"Sending {concurrency} concurrent requests with unique markers...")
    print("─" * 80)
    
    url = f"{base_url}/v1/chat/completions"
    
    # Send requests with unique number markers
    async with httpx.AsyncClient(timeout=60.0) as client:
        tasks = []
        expected = []
        
        for i in range(concurrency):
            marker = f"MARKER_{i:03d}"
            expected.append(marker)
            
            task = client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{
                        "role": "user",
                        "content": f"Say only this word: {marker}. Nothing else."
                    }],
                    "temperature": 0.1,
                    "max_tokens": 20
                }
            )
            tasks.append(task)
        
        start = time.time()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        duration = time.time() - start
        
        print(f"Completed {concurrency} requests in {duration:.1f}s")
        print("─" * 80)
        
        # Check for mixups
        mixup_count = 0
        success_count = 0
        
        for i, (result, expected_marker) in enumerate(zip(results, expected)):
            if isinstance(result, Exception):
                print(f"Request {i}: ❌ Error: {result}")
                continue
            
            if result.status_code != 200:
                print(f"Request {i}: ❌ HTTP {result.status_code}")
                continue
            
            try:
                body = result.json()
                content = body["choices"][0]["message"]["content"]
                
                # Check if response contains the expected marker
                if expected_marker in content:
                    print(f"Request {i}: ✅ Got correct response ({expected_marker})")
                    success_count += 1
                else:
                    # Check if it contains ANY other marker
                    wrong_marker = None
                    for j, other_marker in enumerate(expected):
                        if j != i and other_marker in content:
                            wrong_marker = (j, other_marker)
                            break
                    
                    if wrong_marker:
                        print(f"Request {i}: 🚨 MIXUP! Expected {expected_marker}, got {wrong_marker[1]} (from request {wrong_marker[0]})")
                        mixup_count += 1
                    else:
                        print(f"Request {i}: ⚠️  Unexpected response: {content[:50]}")
                        
            except Exception as e:
                print(f"Request {i}: ❌ Parse error: {e}")
        
        print()
        print("─" * 80)
        print(f"Results: {success_count}/{concurrency} correct, {mixup_count} mixups detected")
        
        if mixup_count > 0:
            print("🚨 RESPONSE MIXUP DETECTED!")
            print("Concurrent requests are getting each other's responses!")
        else:
            print("✅ No mixups detected in this test")
        
        print("─" * 80)
        
        return mixup_count > 0


async def test_session_sharing_across_keys(
    base_url: str,
    api_key1: str,
    api_key2: str
):
    """
    Test if different API keys can end up with the same session ID.
    This would be a critical bug.
    """
    print()
    print("╔" + "═" * 78 + "╗")
    print("║" + " " * 24 + "SESSION SHARING TEST" + " " * 33 + "║")
    print("╚" + "═" * 78 + "╝")
    print()
    print("Checking if different API keys get same session ID...")
    print("─" * 80)
    
    # This test requires access to session IDs in responses
    # May need to enable debug mode or check logs
    print("⚠️  This test requires session_id in API response or log access")
    print("    Check your logs for session_id values for each API key")
    print()
    print("Look for lines like:")
    print('  {"api_key_id": 40, "session_id": "0xa505..."}')
    print('  {"api_key_id": 41, "session_id": "0xa505..."}  ← Same session = BUG!')
    print("─" * 80)


def main():
    parser = argparse.ArgumentParser(
        description='Test for response routing mixups (CRITICAL SECURITY TEST)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '--api-key1',
        required=True,
        help='First API key for testing'
    )
    
    parser.add_argument(
        '--api-key2',
        help='Second API key for cross-key test (highly recommended!)'
    )
    
    parser.add_argument(
        '--base-url',
        default='https://api.mor.org',
        help='API base URL (default: https://api.mor.org)'
    )
    
    parser.add_argument(
        '--model',
        default='qwen3-235b',
        help='Model to test (default: qwen3-235b)'
    )
    
    parser.add_argument(
        '--concurrency',
        type=int,
        default=5,
        help='Number of concurrent requests for same-key test (default: 5)'
    )
    
    args = parser.parse_args()
    
    try:
        # Test 1: Cross-key mixup (if two keys provided)
        if args.api_key2:
            mixup = asyncio.run(test_cross_key_mixup(
                args.base_url,
                args.api_key1,
                args.api_key2,
                args.model
            ))
            
            if mixup:
                print("\n🚨🚨🚨 STOP! Critical security issue detected! 🚨🚨🚨\n")
                return 1
        
        # Test 2: Same-key concurrent mixup
        mixup = asyncio.run(test_same_key_concurrent_mixup(
            args.base_url,
            args.api_key1,
            args.concurrency,
            args.model
        ))
        
        if mixup:
            print("\n🚨 Response mixup detected! Fix before production!\n")
            return 1
        
        # Test 3: Session sharing check
        if args.api_key2:
            asyncio.run(test_session_sharing_across_keys(
                args.base_url,
                args.api_key1,
                args.api_key2
            ))
        
        print("\n✅ All tests passed (but check logs for session sharing)\n")
        return 0
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted\n")
        return 1
    except Exception as e:
        print(f"\n\n❌ Error: {e}\n")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    exit(main())

