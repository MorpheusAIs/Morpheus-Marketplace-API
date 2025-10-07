#!/usr/bin/env python3
"""
Check legacy API key details in the database
"""
import asyncio
import sys
from sqlalchemy import select
from src.db.session import async_session
from src.db.models import APIKey
from src.core.security import verify_api_key

async def check_api_key(prefix: str):
    """Check API key details for debugging"""
    async with async_session() as db:
        query = select(APIKey).where(APIKey.key_prefix == prefix)
        result = await db.execute(query)
        api_key = result.scalar_one_or_none()
        
        if not api_key:
            print(f"❌ No API key found with prefix: {prefix}")
            return
        
        print(f"\n🔍 API Key Details for prefix: {prefix}")
        print(f"=" * 60)
        print(f"ID: {api_key.id}")
        print(f"Key Prefix: {api_key.key_prefix}")
        print(f"Name: {api_key.name}")
        print(f"User ID: {api_key.user_id}")
        print(f"Created At: {api_key.created_at}")
        print(f"Is Active: {api_key.is_active}")
        print(f"Is Default: {api_key.is_default}")
        print(f"\n🔐 Storage Details:")
        print(f"Has hashed_key: {bool(api_key.hashed_key)}")
        print(f"Has encrypted_key: {bool(api_key.encrypted_key)}")
        print(f"Encryption Version: {api_key.encryption_version}")
        
        if api_key.hashed_key:
            print(f"\nHashed key (first 50 chars): {api_key.hashed_key[:50]}...")
        
        if not api_key.encrypted_key:
            print(f"\n⚠️  LEGACY KEY: No encrypted_key - requires manual verification")
        
        # Test verification with common formats
        print(f"\n🧪 Testing Key Verification:")
        print(f"Enter the full API key to test (or press Enter to skip): ", end='')
        test_key = input().strip()
        
        if test_key:
            # Test as-is
            result1 = verify_api_key(test_key, api_key.hashed_key)
            print(f"  ✓ Test with full key: {result1}")
            
            # Test without the period separator (in case it was added later)
            if '.' in test_key:
                test_key_no_period = test_key.replace('.', '')
                result2 = verify_api_key(test_key_no_period, api_key.hashed_key)
                print(f"  ✓ Test without period: {result2}")
            
            # Show what gets truncated for hashing
            print(f"\n📝 Truncation Details:")
            print(f"  Full key: {test_key}")
            print(f"  After removing 'sk-': {test_key[3:]}")
            print(f"  Length: {len(test_key)}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_legacy_key.py <key_prefix>")
        print("Example: python scripts/check_legacy_key.py sk-TRuPTe")
        sys.exit(1)
    
    prefix = sys.argv[1]
    asyncio.run(check_api_key(prefix))
