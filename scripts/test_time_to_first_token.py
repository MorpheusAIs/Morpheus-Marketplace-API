#!/usr/bin/env python3
"""
Test time-to-first-token for chat completions across environments.
This measures the actual API key verification overhead in the critical path.
"""
import time
import requests
import statistics
from typing import List, Dict
import json

# Environment configurations
ENVIRONMENTS = {
    "PROD": {
        "name": "Production (api.mor.org - no enhancements)",
        "url": "https://api.mor.org/api/v1/chat/completions",
        "api_key": "sk-ZvVUBT.5a129348dcfd1eb00336091bb99af57cec0262ee5a9dbba5e0b7e5186f3eed88",
        "description": "Baseline with bcrypt, no billing/caching enhancements"
    },
    "DEV": {
        "name": "Development (api.dev.mor.org - SHA-256 DEPLOYED)",
        "url": "https://api.dev.mor.org/api/v1/chat/completions",
        "api_key": "sk-1OXgn2.d3062eeb8937ef733cf0be23d8dca24d032fb9c4d39816b8622ed24ed8ffeafb",
        "description": "SHA-256 + billing enhancements + Redis caching (NOW DEPLOYED)"
    },
    "STG": {
        "name": "Staging (api.stg.mor.org - SHA-256)",
        "url": "https://api.stg.mor.org/api/v1/chat/completions",
        "api_key": "sk-Wy6GyB.b5bf0702c038ba20bcc8aca343117796e6c5b2a9dbd3121a633285a536bd91fe",
        "description": "DEV + SHA-256 API key verification"
    }
}

# Test configuration
NUM_ITERATIONS = 60
PROMPT = "What is 2+2?"  # Simple prompt for consistent timing
DELAY_BETWEEN_REQUESTS = 1.0  # 1 second delay to avoid concurrency

def make_chat_request(env_config: dict) -> tuple[bool, float, dict]:
    """
    Make a chat completion request and measure time to first token.
    
    Returns:
        Tuple of (success: bool, time_to_first_token_ms: float, details: dict)
    """
    headers = {
        "Authorization": f"Bearer {env_config['api_key']}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "mistral-31-24b",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": PROMPT}
        ],
        "stream": False
    }
    
    start = time.perf_counter()
    try:
        response = requests.post(
            env_config['url'],
            headers=headers,
            json=payload,
            timeout=30
        )
        elapsed = (time.perf_counter() - start) * 1000  # Convert to ms
        
        success = response.status_code == 200
        
        details = {
            "status_code": response.status_code,
            "elapsed_ms": elapsed
        }
        
        if success:
            try:
                data = response.json()
                details["has_response"] = "choices" in data and len(data.get("choices", [])) > 0
            except:
                details["has_response"] = False
        
        return success, elapsed, details
        
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return False, elapsed, {"error": str(e)}

def run_environment_test(env_name: str, env_config: dict, iterations: int) -> dict:
    """
    Run timing tests for a specific environment.
    
    Returns:
        Dict with statistics
    """
    print(f"\n{'='*70}")
    print(f"{env_config['name']}")
    print(f"{'='*70}")
    print(f"Description: {env_config['description']}")
    print(f"URL: {env_config['url']}")
    print(f"API Key: {env_config['api_key'][:15]}...")
    print(f"Iterations: {iterations}")
    print(f"{'─'*70}")
    
    timings: List[float] = []
    successes = 0
    failures = []
    
    for i in range(1, iterations + 1):
        success, elapsed, details = make_chat_request(env_config)
        
        if success:
            successes += 1
            timings.append(elapsed)
            status = "✅"
        else:
            status = "❌"
            failures.append(details)
        
        print(f"  {i:2d}. {status} {elapsed:7.2f}ms", end="")
        if not success:
            print(f" - Status: {details.get('status_code', 'N/A')}")
        else:
            print()
        
        # Delay between requests to avoid concurrency
        if i < iterations:
            time.sleep(DELAY_BETWEEN_REQUESTS)
    
    if not timings:
        print("\n⚠️  No successful requests to analyze")
        return {"success_rate": 0, "failures": failures}
    
    # Calculate statistics
    sorted_timings = sorted(timings)
    stats = {
        "environment": env_name,
        "count": len(timings),
        "success_rate": (successes / iterations) * 100,
        "min": min(timings),
        "max": max(timings),
        "mean": statistics.mean(timings),
        "median": statistics.median(timings),
        "stdev": statistics.stdev(timings) if len(timings) > 1 else 0,
        "p50": sorted_timings[int(len(sorted_timings) * 0.50)],
        "p95": sorted_timings[int(len(sorted_timings) * 0.95)],
        "p99": sorted_timings[int(len(sorted_timings) * 0.99)],
        "failures": failures
    }
    
    # Print summary
    print(f"\n{'─'*70}")
    print(f"📊 Statistics:")
    print(f"   Success Rate:  {stats['success_rate']:.1f}% ({stats['count']}/{iterations})")
    print(f"   Min:           {stats['min']:7.2f}ms")
    print(f"   Max:           {stats['max']:7.2f}ms")
    print(f"   Mean:          {stats['mean']:7.2f}ms  ← Average time to first token")
    print(f"   Median:        {stats['median']:7.2f}ms")
    print(f"   Std Dev:       {stats['stdev']:7.2f}ms")
    print(f"   P95:           {stats['p95']:7.2f}ms")
    
    return stats

def print_comparison(all_stats: dict):
    """Print side-by-side comparison of all environments."""
    print(f"\n{'='*70}")
    print("📈 HEAD-TO-HEAD COMPARISON")
    print(f"{'='*70}\n")
    
    # Create comparison table
    print(f"{'Metric':<20} {'PROD':>15} {'DEV':>15} {'STG':>15}")
    print(f"{'─'*20} {'─'*15} {'─'*15} {'─'*15}")
    
    metrics = ["mean", "median", "min", "max", "p95", "stdev"]
    metric_names = {
        "mean": "Mean (avg)",
        "median": "Median",
        "min": "Min",
        "max": "Max",
        "p95": "P95",
        "stdev": "Std Dev"
    }
    
    for metric in metrics:
        prod_val = all_stats.get("PROD", {}).get(metric, 0)
        dev_val = all_stats.get("DEV", {}).get(metric, 0)
        stg_val = all_stats.get("STG", {}).get(metric, 0)
        
        print(f"{metric_names[metric]:<20} {prod_val:>12.2f}ms {dev_val:>12.2f}ms {stg_val:>12.2f}ms")
    
    # Calculate improvements
    print(f"\n{'='*70}")
    print("🚀 IMPROVEMENTS vs PRODUCTION")
    print(f"{'='*70}\n")
    
    if all_stats.get("PROD") and all_stats.get("PROD", {}).get("mean", 0) > 0:
        prod_mean = all_stats["PROD"]["mean"]
        
        if all_stats.get("DEV") and all_stats["DEV"].get("mean", 0) > 0:
            dev_mean = all_stats["DEV"]["mean"]
            dev_improvement = ((prod_mean - dev_mean) / prod_mean) * 100
            dev_speedup = prod_mean / dev_mean if dev_mean > 0 else 0
            print(f"DEV vs PROD:")
            print(f"  Mean: {prod_mean:.2f}ms → {dev_mean:.2f}ms")
            print(f"  Improvement: {dev_improvement:+.1f}% ({dev_speedup:.2f}× speedup)")
            print(f"  Time saved: {prod_mean - dev_mean:.2f}ms per request")
        
        if all_stats.get("STG") and all_stats["STG"].get("mean", 0) > 0:
            stg_mean = all_stats["STG"]["mean"]
            stg_improvement = ((prod_mean - stg_mean) / prod_mean) * 100
            stg_speedup = prod_mean / stg_mean if stg_mean > 0 else 0
            print(f"\nSTG vs PROD:")
            print(f"  Mean: {prod_mean:.2f}ms → {stg_mean:.2f}ms")
            print(f"  Improvement: {stg_improvement:+.1f}% ({stg_speedup:.2f}× speedup)")
            print(f"  Time saved: {prod_mean - stg_mean:.2f}ms per request")
        
        if all_stats.get("DEV") and all_stats.get("STG"):
            dev_mean = all_stats["DEV"]["mean"]
            stg_mean = all_stats["STG"]["mean"]
            if dev_mean > 0:
                stg_vs_dev = ((dev_mean - stg_mean) / dev_mean) * 100
                stg_vs_dev_speedup = dev_mean / stg_mean if stg_mean > 0 else 0
                print(f"\nSTG vs DEV (SHA-256 impact):")
                print(f"  Mean: {dev_mean:.2f}ms → {stg_mean:.2f}ms")
                print(f"  Improvement: {stg_vs_dev:+.1f}% ({stg_vs_dev_speedup:.2f}× speedup)")
                print(f"  Time saved: {dev_mean - stg_mean:.2f}ms per request")

def main():
    """Run all tests and compare results."""
    print("\n" + "="*70)
    print("TIME TO FIRST TOKEN - HEAD-TO-HEAD COMPARISON")
    print("="*70)
    print(f"Test Time:    {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Prompt:       '{PROMPT}'")
    print(f"Model:        mistral-31-24b")
    print(f"Iterations:   {NUM_ITERATIONS} per environment")
    print(f"Test Type:    Non-streaming chat completion")
    
    all_stats = {}
    
    # Test each environment
    for env_name in ["PROD", "DEV", "STG"]:
        env_config = ENVIRONMENTS[env_name]
        stats = run_environment_test(env_name, env_config, NUM_ITERATIONS)
        all_stats[env_name] = stats
        
        # Short pause between environments
        if env_name != "STG":
            print(f"\n⏸️  Waiting 5 seconds before testing next environment...")
            time.sleep(5)
    
    # Print comparison
    print_comparison(all_stats)
    
    print(f"\n{'='*70}")
    print("✅ Test Complete")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    main()
