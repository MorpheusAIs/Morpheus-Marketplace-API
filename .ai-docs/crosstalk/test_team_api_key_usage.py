"""
Test script simulating multiple team members using the same API key concurrently.

This simulates the HIGH-RISK scenario: Multiple developers testing simultaneously
with the same shared API key.

Usage:
    python test_team_api_key_usage.py --api-key SHARED_TEAM_KEY --team-size 5
"""

import asyncio
import argparse
import json
import time
from typing import List, Dict, Any
import httpx
from datetime import datetime


class TeamApiKeyTester:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.results: List[Dict[str, Any]] = []

    async def developer_request(
        self, 
        developer_id: int, 
        prompt: str, 
        delay_ms: int = 0
    ) -> Dict[str, Any]:
        """Simulate a single developer's request."""
        
        # Simulate developer thinking/typing time
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)
        
        url = f"{self.base_url}/api/v1/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Developer-ID": f"dev-{developer_id}"  # For tracking
        }
        
        payload = {
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "max_tokens": 50
        }
        
        start_time = time.time()
        timestamp = datetime.utcnow().isoformat()
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                print(f"[{timestamp}] Dev {developer_id}: Sending request...")
                response = await client.post(url, headers=headers, json=payload)
                elapsed = time.time() - start_time
                
                result = {
                    "developer_id": developer_id,
                    "prompt": prompt,
                    "status_code": response.status_code,
                    "elapsed_time": elapsed,
                    "timestamp": timestamp,
                    "success": response.status_code == 200
                }
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        result["response"] = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        result["response_preview"] = result["response"][:100]
                        result["session_id"] = data.get("session_id", "unknown")
                        print(f"[{datetime.utcnow().isoformat()}] Dev {developer_id}: ✅ Got response in {elapsed:.2f}s")
                    except json.JSONDecodeError:
                        result["response"] = response.text
                        result["success"] = False
                        result["error"] = "Invalid JSON response"
                        print(f"[{datetime.utcnow().isoformat()}] Dev {developer_id}: ❌ Invalid JSON")
                else:
                    result["error"] = response.text
                    print(f"[{datetime.utcnow().isoformat()}] Dev {developer_id}: ❌ Error {response.status_code}")
                
                return result
                
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"[{datetime.utcnow().isoformat()}] Dev {developer_id}: ❌ Exception: {str(e)}")
            return {
                "developer_id": developer_id,
                "prompt": prompt,
                "status_code": None,
                "elapsed_time": elapsed,
                "timestamp": timestamp,
                "success": False,
                "error": str(e)
            }

    async def test_simultaneous_team_usage(self, team_size: int = 5) -> Dict[str, Any]:
        """
        Simulate multiple team members hitting the API simultaneously with same key.
        
        This is the HIGH-RISK scenario for crosstalk.
        """
        print(f"\n{'='*80}")
        print(f"TEST: Simultaneous Team API Key Usage")
        print(f"Team Size: {team_size} developers")
        print(f"API Key: {self.api_key[:20]}...")
        print(f"{'='*80}\n")
        
        # Different prompts that should have DISTINCT, IDENTIFIABLE answers
        test_prompts = [
            "What is 5 + 3? Reply with ONLY the number.",
            "What color is grass? Reply with ONLY the color.",
            "What is the capital of Italy? Reply with ONLY the city name.",
            "How many legs does a cat have? Reply with ONLY the number.",
            "What day comes after Monday? Reply with ONLY the day name.",
            "What is 10 - 7? Reply with ONLY the number.",
            "What color is snow? Reply with ONLY the color.",
            "What is the capital of Japan? Reply with ONLY the city name.",
            "How many wheels does a bicycle have? Reply with ONLY the number.",
            "What month comes after January? Reply with ONLY the month name.",
        ]
        
        # Assign prompts to developers
        prompts = test_prompts[:team_size]
        
        print("🚀 Launching all requests SIMULTANEOUSLY...\n")
        start_time = time.time()
        
        # Launch ALL requests at the same time (no delays)
        tasks = [
            self.developer_request(i, prompt, delay_ms=0) 
            for i, prompt in enumerate(prompts)
        ]
        results = await asyncio.gather(*tasks)
        
        total_time = time.time() - start_time
        
        # Analyze results
        print(f"\n{'='*80}")
        print(f"RESULTS ANALYSIS")
        print(f"{'='*80}\n")
        print(f"Total test time: {total_time:.2f}s\n")
        
        # Expected answers (simplified validation)
        expected_answers = {
            "5 + 3": "8",
            "color is grass": "green",
            "capital of Italy": "Rome",
            "legs does a cat": "4",
            "after Monday": "Tuesday",
            "10 - 7": "3",
            "color is snow": "white",
            "capital of Japan": "Tokyo",
            "wheels does a bicycle": "2",
            "after January": "February",
        }
        
        all_correct = True
        crosstalk_detected = False
        session_ids = set()
        
        for result in results:
            dev_id = result['developer_id']
            prompt = result['prompt']
            print(f"Developer {dev_id}:")
            print(f"  Prompt: {prompt}")
            print(f"  Status: {result['status_code']}")
            print(f"  Time: {result['elapsed_time']:.2f}s")
            
            if result['success']:
                response = result['response'].strip()
                print(f"  Response: '{response}'")
                
                # Track session IDs
                if 'session_id' in result:
                    session_ids.add(result['session_id'])
                    print(f"  Session ID: {result['session_id'][:16]}...")
                
                # Validate response
                is_correct = False
                expected = None
                for key, expected_answer in expected_answers.items():
                    if key in prompt.lower():
                        expected = expected_answer
                        if expected_answer.lower() in response.lower():
                            is_correct = True
                            print(f"  ✅ Correct answer (expected '{expected}')")
                        else:
                            is_correct = False
                            print(f"  ❌ WRONG answer (expected '{expected}', got '{response}')")
                            
                            # Check if this is another developer's answer (crosstalk!)
                            for other_expected in expected_answers.values():
                                if other_expected.lower() in response.lower() and other_expected != expected:
                                    print(f"  🚨 CROSSTALK DETECTED: Got answer '{other_expected}' from different request!")
                                    crosstalk_detected = True
                        break
                
                all_correct = all_correct and is_correct
            else:
                print(f"  ❌ ERROR: {result.get('error', 'Unknown error')}")
                all_correct = False
            
            print()
        
        # Session analysis
        print(f"{'='*80}")
        print(f"SESSION ANALYSIS")
        print(f"{'='*80}\n")
        print(f"Unique session IDs used: {len(session_ids)}")
        if len(session_ids) == 1:
            print(f"⚠️  ALL requests used the SAME session ID!")
            print(f"   Session: {list(session_ids)[0][:16]}...")
            print(f"   This means all requests went to the SAME provider.")
            print(f"   HIGH RISK for crosstalk if provider doesn't isolate by request_id.")
        else:
            print(f"✅ Requests used {len(session_ids)} different sessions")
            for sid in session_ids:
                print(f"   - {sid[:16]}...")
        
        # Verdict
        print(f"\n{'='*80}")
        print(f"VERDICT")
        print(f"{'='*80}\n")
        
        if crosstalk_detected:
            print("🚨 CROSSTALK DETECTED!")
            print("   Responses were mixed between different requests.")
            print("   This confirms the crosstalk vulnerability.")
        elif not all_correct:
            print("⚠️  INCORRECT RESPONSES")
            print("   Some responses were wrong, but not clearly from other requests.")
            print("   Could be LLM error or potential crosstalk.")
        elif len(session_ids) == 1:
            print("⚠️  POTENTIAL CROSSTALK RISK")
            print("   All requests used the same session, but responses were correct this time.")
            print("   This doesn't guarantee no crosstalk - it could happen under different timing.")
        else:
            print("✅ NO CROSSTALK DETECTED")
            print("   Responses were correct and properly isolated.")
        
        return {
            "test_name": "simultaneous_team_usage",
            "passed": all_correct and not crosstalk_detected,
            "crosstalk_detected": crosstalk_detected,
            "unique_sessions": len(session_ids),
            "same_session_used": len(session_ids) == 1,
            "team_size": team_size,
            "results": results
        }

    async def test_rapid_fire_single_developer(self, num_requests: int = 10) -> Dict[str, Any]:
        """
        Simulate a single developer rapid-firing requests (e.g., testing/debugging).
        """
        print(f"\n{'='*80}")
        print(f"TEST: Rapid-Fire Single Developer")
        print(f"Number of requests: {num_requests}")
        print(f"{'='*80}\n")
        
        prompts = [
            f"Count to {i}. Reply with just the numbers." 
            for i in range(1, num_requests + 1)
        ]
        
        print("🚀 Rapid-firing requests with 100ms spacing...\n")
        
        results = []
        for i, prompt in enumerate(prompts):
            result = await self.developer_request(i, prompt, delay_ms=100)
            results.append(result)
        
        success_count = sum(1 for r in results if r['success'])
        
        print(f"\n{'='*80}")
        print(f"RAPID-FIRE RESULTS")
        print(f"{'='*80}\n")
        print(f"Successful: {success_count}/{num_requests}")
        print(f"Failed: {num_requests - success_count}/{num_requests}")
        
        return {
            "test_name": "rapid_fire_single_developer",
            "passed": success_count == num_requests,
            "success_rate": success_count / num_requests,
            "results": results
        }

    async def run_all_tests(self, team_size: int = 5) -> Dict[str, Any]:
        """Run all team API key tests."""
        print(f"\n{'#'*80}")
        print(f"# TEAM API KEY USAGE TEST SUITE")
        print(f"# Simulating: {team_size} team members using same API key")
        print(f"# API Gateway: {self.base_url}")
        print(f"# API Key: {self.api_key[:20]}...")
        print(f"{'#'*80}")
        
        test_results = []
        
        # Test 1: Simultaneous team usage (HIGH RISK)
        result1 = await self.test_simultaneous_team_usage(team_size)
        test_results.append(result1)
        await asyncio.sleep(2)
        
        # Test 2: Rapid-fire single developer
        result2 = await self.test_rapid_fire_single_developer(num_requests=5)
        test_results.append(result2)
        
        # Summary
        print(f"\n{'='*80}")
        print(f"TEST SUMMARY")
        print(f"{'='*80}")
        
        for result in test_results:
            status = "✅ PASS" if result['passed'] else "❌ FAIL"
            print(f"  {status}  {result['test_name']}")
            if 'crosstalk_detected' in result and result['crosstalk_detected']:
                print(f"         🚨 CROSSTALK DETECTED!")
        
        passed = sum(1 for r in test_results if r['passed'])
        total = len(test_results)
        
        print(f"\n  Overall: {passed}/{total} tests passed")
        
        # Specific crosstalk warning
        if any(r.get('crosstalk_detected', False) for r in test_results):
            print(f"\n{'='*80}")
            print(f"🚨 CROSSTALK VULNERABILITY CONFIRMED")
            print(f"{'='*80}")
            print(f"The system is mixing responses between concurrent requests.")
            print(f"This will cause issues in production with multiple users.")
            print(f"\nRecommended immediate action:")
            print(f"1. Implement advisory locking (temporary fix)")
            print(f"2. Move to session-per-model or session pooling (permanent fix)")
            print(f"3. See CROSSTALK_QUICK_REFERENCE.md for details")
        
        return {
            "summary": {
                "passed": passed,
                "failed": total - passed,
                "total": total,
                "crosstalk_detected": any(r.get('crosstalk_detected', False) for r in test_results)
            },
            "tests": test_results
        }


async def main():
    parser = argparse.ArgumentParser(
        description="Test team API key usage for crosstalk issues"
    )
    parser.add_argument(
        "--api-key",
        required=True,
        help="Shared API key used by team"
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the API Gateway (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--team-size",
        type=int,
        default=5,
        help="Number of concurrent team members to simulate (default: 5)"
    )
    parser.add_argument(
        "--output",
        help="Output file for detailed results (JSON)"
    )
    
    args = parser.parse_args()
    
    tester = TeamApiKeyTester(args.base_url, args.api_key)
    results = await tester.run_all_tests(team_size=args.team_size)
    
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nDetailed results saved to: {args.output}")
    
    # Exit code based on crosstalk detection
    if results['summary'].get('crosstalk_detected', False):
        print(f"\n🚨 EXITING WITH ERROR: Crosstalk detected")
        return 2
    elif results['summary']['failed'] > 0:
        print(f"\n⚠️  EXITING WITH WARNING: Some tests failed")
        return 1
    else:
        print(f"\n✅ EXITING SUCCESS: No issues detected")
        return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)

