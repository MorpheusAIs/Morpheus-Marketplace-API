#!/usr/bin/env python3
"""
External verification script for the Direct Model Fetching Service.

This script can be used to verify that the model fetching service is working
correctly from outside the ECS container, simulating external health checks.

Usage:
    python scripts/verify_model_service.py --url https://api.dev.mor.org
    python scripts/verify_model_service.py --url http://localhost:8000 --verbose
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from typing import Dict, Any, List

import httpx


class ModelServiceVerifier:
    """External verifier for the Direct Model Service."""
    
    def __init__(self, base_url: str, verbose: bool = False):
        self.base_url = base_url.rstrip('/')
        self.verbose = verbose
        self.results: Dict[str, Any] = {}
        
    def log(self, message: str, level: str = "INFO"):
        """Log a message with timestamp."""
        timestamp = datetime.now().isoformat()
        if self.verbose or level in ["ERROR", "WARN"]:
            print(f"[{timestamp}] [{level}] {message}")
    
    async def verify_health_endpoint(self) -> Dict[str, Any]:
        """Verify the main health endpoint includes model service info."""
        self.log("🔍 Checking main health endpoint...")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/health", timeout=10.0)
                response.raise_for_status()
                
                health_data = response.json()
                
                # Check if model service info is present
                model_service = health_data.get("model_service", {})
                
                result = {
                    "status": "healthy" if model_service.get("status") == "healthy" else "unhealthy",
                    "model_count": model_service.get("model_count", 0),
                    "active_models_url": model_service.get("active_models_url", "unknown"),
                    "cache_info": model_service.get("cache_info", {}),
                    "response_time_ms": response.elapsed.total_seconds() * 1000
                }
                
                if result["status"] == "healthy":
                    self.log(f"✅ Health endpoint healthy with {result['model_count']} models")
                else:
                    self.log(f"❌ Health endpoint unhealthy: {model_service.get('status')}", "ERROR")
                
                return result
                
        except Exception as e:
            self.log(f"❌ Health endpoint error: {e}", "ERROR")
            return {"status": "error", "error": str(e)}
    
    async def verify_model_health_endpoint(self) -> Dict[str, Any]:
        """Verify the dedicated model health endpoint."""
        self.log("🔍 Checking model health endpoint...")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/health/models", timeout=15.0)
                response.raise_for_status()
                
                health_data = response.json()
                
                result = {
                    "status": health_data.get("status", "unknown"),
                    "model_counts": health_data.get("model_counts", {}),
                    "cache_stats": health_data.get("cache_stats", {}),
                    "test_results": health_data.get("test_results", {}),
                    "available_models_sample": health_data.get("available_models", [])[:5],
                    "response_time_ms": response.elapsed.total_seconds() * 1000
                }
                
                if result["status"] == "healthy":
                    total_models = result["model_counts"].get("total_models", 0)
                    self.log(f"✅ Model health endpoint healthy with {total_models} total models")
                    
                    # Check test results
                    test_results = result["test_results"]
                    for model, test_result in test_results.items():
                        if test_result.get("status") == "resolved":
                            self.log(f"  ✅ Test model '{model}' resolved to {test_result.get('blockchain_id', 'unknown')}")
                        elif test_result.get("status") == "not_found":
                            self.log(f"  ⚠️ Test model '{model}' not found (expected for some models)", "WARN")
                        else:
                            self.log(f"  ❌ Test model '{model}' error: {test_result.get('error', 'unknown')}", "ERROR")
                else:
                    self.log(f"❌ Model health endpoint unhealthy: {health_data.get('error', 'unknown')}", "ERROR")
                
                return result
                
        except Exception as e:
            self.log(f"❌ Model health endpoint error: {e}", "ERROR")
            return {"status": "error", "error": str(e)}
    
    async def verify_models_list_endpoint(self) -> Dict[str, Any]:
        """Verify the models list endpoint returns active models."""
        self.log("🔍 Checking models list endpoint...")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/api/v1/models", timeout=15.0)
                response.raise_for_status()
                
                models_data = response.json()
                models_list = models_data.get("data", [])
                
                result = {
                    "status": "healthy" if len(models_list) > 0 else "no_models",
                    "model_count": len(models_list),
                    "sample_models": [
                        {
                            "id": model.get("id", "unknown"),
                            "blockchainID": model.get("blockchainID", "unknown")
                        }
                        for model in models_list[:5]
                    ],
                    "response_time_ms": response.elapsed.total_seconds() * 1000
                }
                
                if result["status"] == "healthy":
                    self.log(f"✅ Models endpoint healthy with {result['model_count']} models")
                    for model in result["sample_models"]:
                        self.log(f"  📋 Sample model: {model['id']} -> {model['blockchainID']}")
                else:
                    self.log(f"⚠️ Models endpoint returned no models", "WARN")
                
                return result
                
        except Exception as e:
            self.log(f"❌ Models endpoint error: {e}", "ERROR")
            return {"status": "error", "error": str(e)}
    
    async def test_model_resolution(self) -> Dict[str, Any]:
        """Test model resolution by making a chat completion request."""
        self.log("🔍 Testing model resolution via chat completions...")
        
        test_models = ["venice-uncensored", "mistral-31-24b", "nonexistent-model"]
        results = {}
        
        for model in test_models:
            self.log(f"  🧪 Testing model: {model}")
            try:
                # Note: This will likely fail with auth errors, but we can check
                # if the model resolution is working by examining the error response
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.base_url}/api/v1/chat/completions",
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": "test"}],
                            "max_tokens": 1
                        },
                        timeout=10.0
                    )
                    
                    # If we get here, the model was resolved successfully
                    results[model] = {
                        "status": "resolved_successfully",
                        "response_code": response.status_code
                    }
                    
            except httpx.HTTPStatusError as e:
                # Check if it's an auth error (model resolved) or model error
                if e.response.status_code in [401, 403]:
                    results[model] = {
                        "status": "resolved_but_auth_failed",
                        "response_code": e.response.status_code,
                        "note": "Model resolved successfully, auth required"
                    }
                    self.log(f"    ✅ Model '{model}' resolved (auth error expected)")
                elif e.response.status_code in [400, 422]:
                    try:
                        error_detail = e.response.json()
                        results[model] = {
                            "status": "model_resolution_error",
                            "response_code": e.response.status_code,
                            "error": error_detail
                        }
                        self.log(f"    ❌ Model '{model}' resolution failed: {error_detail}", "ERROR")
                    except:
                        results[model] = {
                            "status": "unknown_error",
                            "response_code": e.response.status_code
                        }
                else:
                    results[model] = {
                        "status": "http_error",
                        "response_code": e.response.status_code
                    }
                    
            except Exception as e:
                results[model] = {
                    "status": "request_error",
                    "error": str(e)
                }
                self.log(f"    ❌ Model '{model}' request error: {e}", "ERROR")
        
        return {
            "status": "completed",
            "test_results": results
        }
    
    async def run_verification(self) -> Dict[str, Any]:
        """Run complete verification suite."""
        self.log(f"🚀 Starting model service verification for {self.base_url}")
        start_time = time.time()
        
        # Run all verification tests
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "base_url": self.base_url,
            "tests": {}
        }
        
        # Health endpoint
        self.results["tests"]["health"] = await self.verify_health_endpoint()
        
        # Model health endpoint
        self.results["tests"]["model_health"] = await self.verify_model_health_endpoint()
        
        # Models list endpoint
        self.results["tests"]["models_list"] = await self.verify_models_list_endpoint()
        
        # Model resolution test
        self.results["tests"]["model_resolution"] = await self.test_model_resolution()
        
        # Calculate overall status
        test_statuses = [test.get("status", "unknown") for test in self.results["tests"].values()]
        healthy_tests = sum(1 for status in test_statuses if status in ["healthy", "resolved_but_auth_failed", "completed"])
        total_tests = len(test_statuses)
        
        self.results["summary"] = {
            "overall_status": "healthy" if healthy_tests >= total_tests - 1 else "unhealthy",
            "healthy_tests": healthy_tests,
            "total_tests": total_tests,
            "duration_seconds": round(time.time() - start_time, 2)
        }
        
        # Log summary
        if self.results["summary"]["overall_status"] == "healthy":
            self.log(f"✅ Verification completed successfully ({healthy_tests}/{total_tests} tests passed)")
        else:
            self.log(f"❌ Verification failed ({healthy_tests}/{total_tests} tests passed)", "ERROR")
        
        return self.results


async def main():
    parser = argparse.ArgumentParser(description="Verify Direct Model Service health")
    parser.add_argument("--url", required=True, help="Base URL of the API (e.g., https://api.dev.mor.org)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--output", "-o", help="Output file for JSON results")
    
    args = parser.parse_args()
    
    # Run verification
    verifier = ModelServiceVerifier(args.url, args.verbose)
    results = await verifier.run_verification()
    
    # Output results
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"Results written to {args.output}")
    
    if args.verbose:
        print("\n" + "="*60)
        print("VERIFICATION RESULTS")
        print("="*60)
        print(json.dumps(results, indent=2))
    
    # Exit with appropriate code
    exit_code = 0 if results["summary"]["overall_status"] == "healthy" else 1
    sys.exit(exit_code)


if __name__ == "__main__":
    asyncio.run(main())
