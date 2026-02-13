#!/usr/bin/env python3
"""
Test API key verification timing with the DEV environment (bcrypt).
Measures end-to-end request latency for API key authentication.
"""
import time
import requests
import statistics
from typing import List

# Configuration
API_KEY = "sk-GpdXp9.e549f98184bfe24dfcc854ccc0a353dd78e1374390edb0d107427eb0b023b0ec"
API_URL = "https://api.dev.mor.org/api/v1/models"
NUM_ITERATIONS = 20

def make_request() -> tuple[bool, float]:
    """
    Make a single API request and measure timing.
    
    Returns:
        Tuple of (success: bool, elapsed_ms: float)
    """
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    start = time.perf_counter()
    try:
        response = requests.get(API_URL, headers=headers, timeout=10)
        elapsed = (time.perf_counter() - start) * 1000  # Convert to ms
        
        success = response.status_code == 200
        return success, elapsed
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        print(f"  ❌ Request failed: {e}")
        return False, elapsed

def run_timing_test(name: str, iterations: int) -> dict:
    """
    Run a series of timing tests.
    
    Returns:
        Dict with statistics
    """
    print(f"\n{'='*60}")
    print(f"{name}")
    print(f"{'='*60}")
    
    timings: List[float] = []
    successes = 0
    
    for i in range(1, iterations + 1):
        success, elapsed = make_request()
        if success:
            successes += 1
            timings.append(elapsed)
            status = "✅"
        else:
            status = "❌"
        
        print(f"  {i:2d}. {status} {elapsed:6.2f}ms")
    
    if not timings:
        print("\n⚠️  No successful requests to analyze")
        return {}
    
    # Calculate statistics
    stats = {
        "count": len(timings),
        "success_rate": (successes / iterations) * 100,
        "min": min(timings),
        "max": max(timings),
        "mean": statistics.mean(timings),
        "median": statistics.median(timings),
        "stdev": statistics.stdev(timings) if len(timings) > 1 else 0,
    }
    
    # Calculate percentiles
    sorted_timings = sorted(timings)
    stats["p50"] = sorted_timings[int(len(sorted_timings) * 0.50)]
    stats["p95"] = sorted_timings[int(len(sorted_timings) * 0.95)]
    stats["p99"] = sorted_timings[int(len(sorted_timings) * 0.99)]
    
    # Print summary
    print(f"\n{'─'*60}")
    print(f"📊 Statistics:")
    print(f"   Success Rate:  {stats['success_rate']:.1f}% ({stats['count']}/{iterations})")
    print(f"   Min:           {stats['min']:6.2f}ms")
    print(f"   Max:           {stats['max']:6.2f}ms")
    print(f"   Mean:          {stats['mean']:6.2f}ms")
    print(f"   Median:        {stats['median']:6.2f}ms")
    print(f"   Std Dev:       {stats['stdev']:6.2f}ms")
    print(f"   P50:           {stats['p50']:6.2f}ms")
    print(f"   P95:           {stats['p95']:6.2f}ms")
    print(f"   P99:           {stats['p99']:6.2f}ms")
    
    return stats

def main():
    """Run all timing tests."""
    print("\n" + "="*60)
    print("API Key Authentication Timing Test - DEV (bcrypt)")
    print("="*60)
    print(f"API URL:      {API_URL}")
    print(f"API Key:      {API_KEY[:15]}...")
    print(f"Iterations:   {NUM_ITERATIONS}")
    print(f"Test Time:    {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Run test cycles
    all_stats = []
    
    for cycle in range(1, 4):  # 3 cycles
        stats = run_timing_test(f"Cycle {cycle}: API Key Verification (bcrypt)", NUM_ITERATIONS)
        if stats:
            all_stats.append(stats)
        
        if cycle < 3:
            print(f"\n⏸️  Waiting 2 seconds before next cycle...")
            time.sleep(2)
    
    # Overall summary
    if all_stats:
        print(f"\n{'='*60}")
        print("📈 Overall Summary (All Cycles)")
        print(f"{'='*60}")
        
        all_means = [s["mean"] for s in all_stats]
        all_medians = [s["median"] for s in all_stats]
        all_p95s = [s["p95"] for s in all_stats]
        
        print(f"  Average Mean:      {statistics.mean(all_means):6.2f}ms")
        print(f"  Average Median:    {statistics.mean(all_medians):6.2f}ms")
        print(f"  Average P95:       {statistics.mean(all_p95s):6.2f}ms")
        print(f"  Best Mean:         {min(all_means):6.2f}ms")
        print(f"  Worst Mean:        {max(all_means):6.2f}ms")
        
        print(f"\n{'='*60}")
        print("✅ Test Complete - DEV Environment")
        print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
