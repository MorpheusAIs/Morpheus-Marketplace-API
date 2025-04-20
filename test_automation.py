#!/usr/bin/env python3
"""
Simple test script to verify the model_mappings.json file and automation features.
"""

import json
import os
import sys
from pathlib import Path

def test_model_mappings():
    """Test that model_mappings.json is properly configured."""
    # Get the project root directory
    root_dir = Path(__file__).parent
    
    # Path to model_mappings.json
    mappings_path = root_dir / "config" / "model_mappings.json"
    
    print(f"\nChecking model_mappings.json at {mappings_path}...")
    
    # Check if file exists
    if not mappings_path.exists():
        print("❌ ERROR: model_mappings.json does not exist!")
        return False
    
    # Load and validate mappings
    try:
        with open(mappings_path, 'r') as f:
            mappings = json.load(f)
        
        # Check required keys
        required_keys = ["default", "gpt-3.5-turbo", "gpt-4", "gpt-4o", "claude-3-opus"]
        missing_keys = [key for key in required_keys if key not in mappings]
        
        if missing_keys:
            print(f"❌ ERROR: Missing required keys in model_mappings.json: {missing_keys}")
            return False
        
        # Check that all values are valid blockchain IDs (starting with 0x)
        invalid_values = [key for key, value in mappings.items() if not value.startswith("0x")]
        
        if invalid_values:
            print(f"❌ ERROR: Invalid blockchain IDs for keys: {invalid_values}")
            return False
        
        # Print summary
        print(f"✅ model_mappings.json is valid with {len(mappings)} mappings")
        print(f"✅ Default model: {mappings['default']}")
        print(f"✅ gpt-3.5-turbo: {mappings['gpt-3.5-turbo']}")
        print(f"✅ gpt-4: {mappings['gpt-4']}")
        print(f"✅ gpt-4o: {mappings['gpt-4o']}")
        print(f"✅ claude-3-opus: {mappings['claude-3-opus']}")
        return True
        
    except json.JSONDecodeError:
        print("❌ ERROR: model_mappings.json is not valid JSON!")
        return False
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        return False

def verify_automation_implementation():
    """Verify the automation implementation components."""
    # Get the project root directory
    root_dir = Path(__file__).parent
    
    print("\nVerifying automation implementation components...")
    
    # Files to check
    files_to_check = [
        "src/db/models.py",
        "src/crud/automation.py",
        "src/core/model_routing.py",
        "src/api/v1/automation.py",
        "src/services/session_service.py",
        "migration/create_automation_settings.sql"
    ]
    
    # Check each file
    all_files_exist = True
    for file_path in files_to_check:
        full_path = root_dir / file_path
        if full_path.exists():
            print(f"✅ {file_path} exists")
        else:
            print(f"❌ ERROR: {file_path} does not exist!")
            all_files_exist = False
    
    return all_files_exist

def main():
    """Run all verification checks."""
    print("=== Automation Feature Verification ===")
    
    mappings_valid = test_model_mappings()
    components_valid = verify_automation_implementation()
    
    print("\n=== Summary ===")
    if mappings_valid:
        print("✅ Model mappings are valid")
    else:
        print("❌ Model mappings have issues")
    
    if components_valid:
        print("✅ All required automation components are present")
    else:
        print("❌ Some automation components are missing")
    
    if mappings_valid and components_valid:
        print("\n✅ Automation feature is properly configured!")
        return 0
    else:
        print("\n❌ Automation feature has configuration issues!")
        return 1

if __name__ == "__main__":
    sys.exit(main()) 