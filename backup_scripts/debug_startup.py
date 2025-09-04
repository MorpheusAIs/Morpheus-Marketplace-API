#!/usr/bin/env python3
"""
Debug script to identify startup issues
Run this to test imports and basic functionality before starting the full app
"""

import sys
import traceback
import time
from datetime import datetime

def log_step(step_name):
    """Log each step with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] ✅ {step_name}")

def log_error(step_name, error):
    """Log errors with details"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] ❌ {step_name}: {error}")
    traceback.print_exc()

def test_basic_imports():
    """Test basic Python imports"""
    try:
        log_step("Testing basic imports...")
        import os
        import json
        import logging
        log_step("Basic imports successful")
        return True
    except Exception as e:
        log_error("Basic imports failed", e)
        return False

def test_fastapi_imports():
    """Test FastAPI and related imports"""
    try:
        log_step("Testing FastAPI imports...")
        from fastapi import FastAPI, APIRouter, HTTPException, status, Depends
        from pydantic import BaseModel, EmailStr, Field
        log_step("FastAPI imports successful")
        return True
    except Exception as e:
        log_error("FastAPI imports failed", e)
        return False

def test_database_imports():
    """Test database-related imports"""
    try:
        log_step("Testing database imports...")
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy import select
        import asyncpg
        log_step("Database imports successful")
        return True
    except Exception as e:
        log_error("Database imports failed", e)
        return False

def test_cognito_imports():
    """Test AWS/Cognito imports"""
    try:
        log_step("Testing AWS/Cognito imports...")
        import boto3
        from botocore.exceptions import ClientError
        log_step("AWS/Cognito imports successful")
        return True
    except Exception as e:
        log_error("AWS/Cognito imports failed", e)
        return False

def test_jose_imports():
    """Test JWT/Jose imports"""
    try:
        log_step("Testing JWT/Jose imports...")
        from jose import jwt, jwk
        from jose.utils import base64url_decode
        log_step("JWT/Jose imports successful")
        return True
    except Exception as e:
        log_error("JWT/Jose imports failed", e)
        return False

def test_app_imports():
    """Test application-specific imports"""
    try:
        log_step("Testing app imports...")
        
        # Test config import
        from src.core.config import settings
        log_step("Config import successful")
        
        # Test database import
        from src.db.database import get_db
        log_step("Database module import successful")
        
        # Test models import
        from src.db.models import User, APIKey
        log_step("Models import successful")
        
        # Test dependencies import
        from src.dependencies import get_current_user
        log_step("Dependencies import successful")
        
        return True
    except Exception as e:
        log_error("App imports failed", e)
        return False

def test_cognito_auth_import():
    """Test the new cognito_auth module"""
    try:
        log_step("Testing cognito_auth module import...")
        from src.api.v1 import cognito_auth
        log_step("Cognito auth module import successful")
        return True
    except Exception as e:
        log_error("Cognito auth module import failed", e)
        return False

def test_main_app_creation():
    """Test creating the main FastAPI app"""
    try:
        log_step("Testing main app creation...")
        from src.main import app
        log_step("Main app creation successful")
        return True
    except Exception as e:
        log_error("Main app creation failed", e)
        return False

def test_environment_variables():
    """Test critical environment variables"""
    try:
        log_step("Testing environment variables...")
        import os
        
        # Check for critical env vars
        critical_vars = [
            'DATABASE_URL',
            'COGNITO_USER_POOL_ID',
            'COGNITO_CLIENT_ID',
            'COGNITO_REGION',
            'JWT_SECRET_KEY'
        ]
        
        missing_vars = []
        for var in critical_vars:
            if not os.getenv(var):
                missing_vars.append(var)
        
        if missing_vars:
            log_error("Missing environment variables", f"Missing: {', '.join(missing_vars)}")
            return False
        
        log_step("Environment variables check successful")
        return True
    except Exception as e:
        log_error("Environment variables check failed", e)
        return False

def main():
    """Run all diagnostic tests"""
    print("🔍 Starting Morpheus API Startup Diagnostics")
    print("=" * 60)
    
    tests = [
        ("Basic Imports", test_basic_imports),
        ("FastAPI Imports", test_fastapi_imports),
        ("Database Imports", test_database_imports),
        ("AWS/Cognito Imports", test_cognito_imports),
        ("JWT/Jose Imports", test_jose_imports),
        ("Environment Variables", test_environment_variables),
        ("App Imports", test_app_imports),
        ("Cognito Auth Import", test_cognito_auth_import),
        ("Main App Creation", test_main_app_creation),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        print(f"\n🧪 Running: {test_name}")
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            log_error(f"{test_name} (unexpected error)", e)
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"📊 Test Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("✅ All tests passed! The app should start successfully.")
        return 0
    else:
        print("❌ Some tests failed. Check the errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
