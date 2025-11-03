"""
Test script to detect session crosstalk issues in the Morpheus Marketplace API.

This script sends concurrent requests using the same API key and checks if responses
are correctly correlated to their respective requests.

Usage:
    python test_session_crosstalk.py --api-key YOUR_API_KEY --base-url http://localhost:8000

Requirements:
    pip install httpx asyncio
"""

import asyncio
import argparse
import json
import time
from typing import List, Dict, Any
import httpx


class CrosstalkTester:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.results: List[Dict[str, Any]] = []

    async def send_request(self, prompt: str, request_id: int, model: str = None) -> Dict[str, Any]:
        """Send a single chat completion request and track the result."""
        url = f"{self.base_url}/api/v1/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "max_tokens": 100
        }
        
        if model:
            payload["model"] = model
        
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                elapsed = time.time() - start_time
                
                result = {
                    "request_id": request_id,
                    "prompt": prompt,
                    "model": model,
                    "status_code": response.status_code,
                    "elapsed_time": elapsed,
                    "success": response.status_code == 200
                }
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        result["response"] = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        result["response_preview"] = result["response"][:100]
                    except json.JSONDecodeError:
                        result["response"] = response.text
                        result["success"] = False
                        result["error"] = "Invalid JSON response"
                else:
                    result["error"] = response.text
                
                return result
                
        except Exception as e:
            elapsed = time.time() - start_time
            return {
                "request_id": request_id,
                "prompt": prompt,
                "model": model,
                "status_code": None,
                "elapsed_time": elapsed,
                "success": False,
                "error": str(e)
            }

    async def test_concurrent_same_model(self, num_requests: int = 3) -> Dict[str, Any]:
        """
        Test 1: Send concurrent requests with the same API key to the same model.
        
        Expected: All responses should match their respective prompts.
        Risk: Response mixing if session is shared without proper request isolation.
        """
        print(f"\n{'='*80}")
        print(f"TEST 1: Concurrent requests with same API key, same model")
        print(f"{'='*80}")
        
        prompts = [
            "What is 2 + 2? Reply with just the number.",
            "What is the capital of France? Reply with just the city name.",
            "What color is the sky? Reply with just the color."
        ][:num_requests]
        
        print(f"\nSending {len(prompts)} concurrent requests...")
        tasks = [self.send_request(prompt, i) for i, prompt in enumerate(prompts)]
        results = await asyncio.gather(*tasks)
        
        # Analyze results
        print(f"\nResults:")
        all_correct = True
        for result in results:
            print(f"\n  Request {result['request_id']}:")
            print(f"    Prompt: {result['prompt']}")
            print(f"    Status: {result['status_code']}")
            print(f"    Time: {result['elapsed_time']:.2f}s")
            
            if result['success']:
                print(f"    Response: {result['response_preview']}")
                
                # Basic validation - check if response makes sense for prompt
                prompt_lower = result['prompt'].lower()
                response_lower = result['response'].lower()
                
                is_correct = True
                if "2 + 2" in prompt_lower and "4" not in response_lower:
                    is_correct = False
                    print(f"    ❌ MISMATCH: Expected '4' in response")
                elif "capital of france" in prompt_lower and "paris" not in response_lower:
                    is_correct = False
                    print(f"    ❌ MISMATCH: Expected 'Paris' in response")
                elif "color is the sky" in prompt_lower and "blue" not in response_lower:
                    is_correct = False
                    print(f"    ❌ MISMATCH: Expected 'blue' in response")
                else:
                    print(f"    ✅ Response matches expected answer")
                
                all_correct = all_correct and is_correct
            else:
                print(f"    ❌ ERROR: {result.get('error', 'Unknown error')}")
                all_correct = False
        
        return {
            "test_name": "concurrent_same_model",
            "passed": all_correct and all(r['success'] for r in results),
            "results": results
        }

    async def test_rapid_model_switching(self) -> Dict[str, Any]:
        """
        Test 2: Rapidly switch between models with the same API key.
        
        Expected: Each request should get a response from the correct model.
        Risk: Responses from old session after model switch.
        """
        print(f"\n{'='*80}")
        print(f"TEST 2: Rapid model switching with same API key")
        print(f"{'='*80}")
        
        # This test assumes different models are available
        # Adjust model names based on your deployment
        test_cases = [
            ("Tell me a number", "gpt-4"),
            ("Tell me a letter", "claude-3"),
            ("Tell me a color", "gpt-4"),
        ]
        
        print(f"\nSending requests with model switches...")
        results = []
        for i, (prompt, model) in enumerate(test_cases):
            print(f"\n  Request {i}: {prompt} (model: {model})")
            result = await self.send_request(prompt, i, model)
            results.append(result)
            print(f"    Status: {result['status_code']}")
            print(f"    Time: {result['elapsed_time']:.2f}s")
            if result['success']:
                print(f"    Response: {result['response_preview']}")
            else:
                print(f"    Error: {result.get('error', 'Unknown')}")
            
            # Small delay between requests
            await asyncio.sleep(0.1)
        
        all_success = all(r['success'] for r in results)
        
        return {
            "test_name": "rapid_model_switching",
            "passed": all_success,
            "results": results
        }

    async def test_concurrent_streaming_nonstreaming(self) -> Dict[str, Any]:
        """
        Test 3: Mix streaming and non-streaming requests concurrently.
        
        Expected: No interference between streaming and non-streaming.
        Risk: Stream corruption or blocking.
        """
        print(f"\n{'='*80}")
        print(f"TEST 3: Concurrent streaming + non-streaming requests")
        print(f"{'='*80}")
        
        # For now, just test non-streaming (streaming requires different handling)
        prompts = [
            "Count to 3",
            "Name 3 colors",
        ]
        
        print(f"\nSending concurrent non-streaming requests...")
        tasks = [self.send_request(prompt, i) for i, prompt in enumerate(prompts)]
        results = await asyncio.gather(*tasks)
        
        all_success = all(r['success'] for r in results)
        
        for result in results:
            print(f"\n  Request {result['request_id']}: {result['prompt']}")
            print(f"    Status: {result['status_code']}")
            if result['success']:
                print(f"    ✅ Success")
            else:
                print(f"    ❌ Error: {result.get('error', 'Unknown')}")
        
        return {
            "test_name": "concurrent_streaming_nonstreaming",
            "passed": all_success,
            "results": results
        }

    async def test_request_id_correlation(self) -> Dict[str, Any]:
        """
        Test 4: Verify that request IDs are properly correlated in logs.
        
        This is more of a log inspection test - check server logs for request_id tracking.
        """
        print(f"\n{'='*80}")
        print(f"TEST 4: Request ID correlation (check server logs)")
        print(f"{'='*80}")
        
        prompt = "Generate a unique identifier for this request"
        print(f"\nSending test request...")
        result = await self.send_request(prompt, 999)
        
        print(f"\n  Status: {result['status_code']}")
        print(f"\n  ℹ️  Check server logs for request_id correlation")
        print(f"     Look for: request_id, event_type, session_id in logs")
        
        return {
            "test_name": "request_id_correlation",
            "passed": result['success'],
            "results": [result],
            "note": "Manual log inspection required"
        }

    async def run_all_tests(self) -> Dict[str, Any]:
        """Run all crosstalk tests."""
        print(f"\n{'#'*80}")
        print(f"# SESSION CROSSTALK TEST SUITE")
        print(f"# API Gateway: {self.base_url}")
        print(f"# API Key: {self.api_key[:20]}...")
        print(f"{'#'*80}")
        
        test_results = []
        
        # Test 1: Concurrent same model
        result1 = await self.test_concurrent_same_model(num_requests=3)
        test_results.append(result1)
        await asyncio.sleep(1)
        
        # Test 2: Rapid model switching (commented out if models aren't configured)
        # result2 = await self.test_rapid_model_switching()
        # test_results.append(result2)
        # await asyncio.sleep(1)
        
        # Test 3: Concurrent streaming + non-streaming
        result3 = await self.test_concurrent_streaming_nonstreaming()
        test_results.append(result3)
        await asyncio.sleep(1)
        
        # Test 4: Request ID correlation
        result4 = await self.test_request_id_correlation()
        test_results.append(result4)
        
        # Summary
        print(f"\n{'='*80}")
        print(f"TEST SUMMARY")
        print(f"{'='*80}")
        
        for result in test_results:
            status = "✅ PASS" if result['passed'] else "❌ FAIL"
            print(f"  {status}  {result['test_name']}")
        
        passed = sum(1 for r in test_results if r['passed'])
        total = len(test_results)
        
        print(f"\n  Overall: {passed}/{total} tests passed")
        
        return {
            "summary": {
                "passed": passed,
                "failed": total - passed,
                "total": total
            },
            "tests": test_results
        }


async def main():
    parser = argparse.ArgumentParser(
        description="Test for session crosstalk issues in Morpheus Marketplace API"
    )
    parser.add_argument(
        "--api-key",
        required=True,
        help="API key to use for testing"
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the API Gateway (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--output",
        help="Output file for detailed results (JSON)"
    )
    
    args = parser.parse_args()
    
    tester = CrosstalkTester(args.base_url, args.api_key)
    results = await tester.run_all_tests()
    
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nDetailed results saved to: {args.output}")
    
    # Exit code based on test results
    if results['summary']['failed'] > 0:
        print(f"\n❌ CROSSTALK ISSUES DETECTED")
        return 1
    else:
        print(f"\n✅ NO CROSSTALK ISSUES DETECTED")
        return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)

