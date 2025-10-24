#!/usr/bin/env python3
"""
Verification script for automation endpoint authentication changes.

This script verifies that the automation endpoints are correctly configured
to use JWT authentication instead of API key authentication.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.api.v1.automation.index import router, get_automation_settings, update_automation_settings
from src.dependencies import get_current_user
from inspect import signature


def verify_authentication():
    """Verify that automation endpoints use correct authentication."""
    
    print("🔍 Verifying Automation Endpoint Authentication")
    print("=" * 60)
    
    errors = []
    warnings = []
    success = []
    
    # Check GET endpoint
    print("\n📋 Checking GET /api/v1/automation/settings...")
    get_sig = signature(get_automation_settings)
    get_params = get_sig.parameters
    
    # Check for current_user parameter with get_current_user dependency
    if 'current_user' in get_params:
        param = get_params['current_user']
        if hasattr(param.default, 'dependency') and param.default.dependency == get_current_user:
            success.append("✅ GET endpoint uses get_current_user (JWT auth)")
            print("   ✅ Uses JWT authentication (get_current_user)")
        else:
            errors.append("❌ GET endpoint has current_user but wrong dependency")
            print("   ❌ current_user parameter has wrong dependency")
    else:
        errors.append("❌ GET endpoint missing current_user parameter")
        print("   ❌ Missing current_user parameter")
    
    # Check for old user parameter (should not exist)
    if 'user' in get_params:
        warnings.append("⚠️ GET endpoint still has 'user' parameter (should be 'current_user')")
        print("   ⚠️ Found old 'user' parameter (should be removed)")
    else:
        success.append("✅ GET endpoint correctly removed old 'user' parameter")
        print("   ✅ No old 'user' parameter found")
    
    # Check PUT endpoint
    print("\n📋 Checking PUT /api/v1/automation/settings...")
    put_sig = signature(update_automation_settings)
    put_params = put_sig.parameters
    
    # Check for current_user parameter with get_current_user dependency
    if 'current_user' in put_params:
        param = put_params['current_user']
        if hasattr(param.default, 'dependency') and param.default.dependency == get_current_user:
            success.append("✅ PUT endpoint uses get_current_user (JWT auth)")
            print("   ✅ Uses JWT authentication (get_current_user)")
        else:
            errors.append("❌ PUT endpoint has current_user but wrong dependency")
            print("   ❌ current_user parameter has wrong dependency")
    else:
        errors.append("❌ PUT endpoint missing current_user parameter")
        print("   ❌ Missing current_user parameter")
    
    # Check for old user parameter (should not exist)
    if 'user' in put_params:
        warnings.append("⚠️ PUT endpoint still has 'user' parameter (should be 'current_user')")
        print("   ⚠️ Found old 'user' parameter (should be removed)")
    else:
        success.append("✅ PUT endpoint correctly removed old 'user' parameter")
        print("   ✅ No old 'user' parameter found")
    
    # Check docstrings
    print("\n📋 Checking endpoint docstrings...")
    
    get_doc = get_automation_settings.__doc__ or ""
    if "JWT Bearer authentication" in get_doc or "JWT" in get_doc:
        success.append("✅ GET endpoint docstring mentions JWT authentication")
        print("   ✅ GET docstring mentions JWT authentication")
    else:
        warnings.append("⚠️ GET endpoint docstring should mention JWT authentication")
        print("   ⚠️ GET docstring should mention JWT authentication")
    
    put_doc = update_automation_settings.__doc__ or ""
    if "JWT Bearer authentication" in put_doc or "JWT" in put_doc:
        success.append("✅ PUT endpoint docstring mentions JWT authentication")
        print("   ✅ PUT docstring mentions JWT authentication")
    else:
        warnings.append("⚠️ PUT endpoint docstring should mention JWT authentication")
        print("   ⚠️ PUT docstring should mention JWT authentication")
    
    # Print summary
    print("\n" + "=" * 60)
    print("📊 VERIFICATION SUMMARY")
    print("=" * 60)
    
    if success:
        print(f"\n✅ Successes ({len(success)}):")
        for item in success:
            print(f"   {item}")
    
    if warnings:
        print(f"\n⚠️ Warnings ({len(warnings)}):")
        for item in warnings:
            print(f"   {item}")
    
    if errors:
        print(f"\n❌ Errors ({len(errors)}):")
        for item in errors:
            print(f"   {item}")
    
    # Final verdict
    print("\n" + "=" * 60)
    if errors:
        print("❌ VERIFICATION FAILED")
        print("   Automation endpoints have configuration errors.")
        return False
    elif warnings:
        print("⚠️ VERIFICATION PASSED WITH WARNINGS")
        print("   Automation endpoints are functional but have minor issues.")
        return True
    else:
        print("✅ VERIFICATION PASSED")
        print("   Automation endpoints are correctly configured for JWT authentication.")
        return True


if __name__ == "__main__":
    print("\n" + "🔐 Morpheus API - Automation Authentication Verification")
    print("=" * 60)
    print("This script verifies that automation endpoints use JWT authentication\n")
    
    try:
        success = verify_authentication()
        print("\n" + "=" * 60)
        if success:
            print("✅ All checks passed! Automation endpoints are ready.")
            sys.exit(0)
        else:
            print("❌ Verification failed. Please review the errors above.")
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ Verification script encountered an error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

