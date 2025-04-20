#!/usr/bin/env python3
"""
Test runner for the Morpheus API Gateway automation feature.
"""

import subprocess
import os
import sys
from pathlib import Path

def run_tests():
    """Run all tests for the automation feature."""
    # Get the project root directory
    root_dir = Path(__file__).parent
    
    # Set environment variables for testing
    env = os.environ.copy()
    env["AUTOMATION_FEATURE_ENABLED"] = "true"
    env["TESTING"] = "true"
    
    # Run unit tests
    print("Running unit tests...")
    unit_result = subprocess.run(
        ["pytest", "-xvs", "tests/unit/"], 
        cwd=root_dir,
        env=env
    )
    
    # Run API tests
    print("\nRunning API tests...")
    api_result = subprocess.run(
        ["pytest", "-xvs", "tests/api/"], 
        cwd=root_dir,
        env=env
    )
    
    # Print results
    print("\n=== Test Results ===")
    print(f"Unit tests: {'PASSED' if unit_result.returncode == 0 else 'FAILED'}")
    print(f"API tests: {'PASSED' if api_result.returncode == 0 else 'FAILED'}")
    
    # Return overall result
    return unit_result.returncode == 0 and api_result.returncode == 0

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1) 