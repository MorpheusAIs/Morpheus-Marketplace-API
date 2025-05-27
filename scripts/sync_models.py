#!/usr/bin/env python3
"""
Script to sync local models.json with active models from the API.
This ensures that the model routing system has access to all currently active models.
"""

import json
import os
import sys
import httpx
import asyncio
from typing import Dict, List, Set

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'src'))

ACTIVE_MODELS_URL = "https://active.mor.org/active_models.json"
LOCAL_MODELS_FILE = "models.json"

async def fetch_active_models() -> List[Dict]:
    """Fetch active models from the API endpoint."""
    print(f"Fetching active models from {ACTIVE_MODELS_URL}...")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(ACTIVE_MODELS_URL, timeout=10.0)
        response.raise_for_status()
        
        data = response.json()
        models = data.get("models", [])
        print(f"Found {len(models)} active models")
        return models

def load_local_models() -> Dict:
    """Load the local models.json file."""
    if not os.path.exists(LOCAL_MODELS_FILE):
        print(f"Local models file {LOCAL_MODELS_FILE} not found!")
        return {"models": []}
    
    with open(LOCAL_MODELS_FILE, 'r') as f:
        data = json.load(f)
    
    print(f"Loaded {len(data.get('models', []))} models from local file")
    return data

def save_local_models(models_data: Dict):
    """Save the models data to the local models.json file."""
    with open(LOCAL_MODELS_FILE, 'w') as f:
        json.dump(models_data, f, indent=2)
    
    print(f"Saved {len(models_data.get('models', []))} models to {LOCAL_MODELS_FILE}")

def sync_models(active_models: List[Dict], local_data: Dict) -> Dict:
    """
    Sync active models with local models.
    
    Strategy:
    1. Keep all existing local models that are not in active models (for backwards compatibility)
    2. Add any active models that are missing from local
    3. Update any existing models with new data from active models
    """
    local_models = local_data.get("models", [])
    
    # Create lookup dictionaries
    active_by_id = {model["Id"]: model for model in active_models}
    active_by_name = {model["Name"]: model for model in active_models}
    
    local_by_id = {model["Id"]: model for model in local_models}
    local_by_name = {model["Name"]: model for model in local_models}
    
    # Track changes
    added_models = []
    updated_models = []
    kept_models = []
    
    # Start with all active models (these are the source of truth)
    synced_models = []
    
    for active_model in active_models:
        model_id = active_model["Id"]
        model_name = active_model["Name"]
        
        if model_id in local_by_id:
            # Model exists locally, check if it needs updating
            local_model = local_by_id[model_id]
            if local_model != active_model:
                synced_models.append(active_model)
                updated_models.append(model_name)
            else:
                synced_models.append(local_model)
                kept_models.append(model_name)
        else:
            # New model from active models
            synced_models.append(active_model)
            added_models.append(model_name)
    
    # Add any local models that are not in active models (for backwards compatibility)
    local_only_models = []
    for local_model in local_models:
        if local_model["Id"] not in active_by_id:
            synced_models.append(local_model)
            local_only_models.append(local_model["Name"])
    
    # Print summary
    print("\nSync Summary:")
    print(f"  ✅ Added models ({len(added_models)}): {', '.join(added_models) if added_models else 'None'}")
    print(f"  🔄 Updated models ({len(updated_models)}): {', '.join(updated_models) if updated_models else 'None'}")
    print(f"  📌 Kept unchanged ({len(kept_models)}): {len(kept_models)} models")
    print(f"  🏠 Local-only models ({len(local_only_models)}): {', '.join(local_only_models) if local_only_models else 'None'}")
    
    return {"models": synced_models}

async def main():
    """Main function to sync models."""
    print("Model Sync Tool")
    print("=" * 50)
    
    try:
        # Fetch active models
        active_models = await fetch_active_models()
        
        # Load local models
        local_data = load_local_models()
        
        # Sync models
        synced_data = sync_models(active_models, local_data)
        
        # Save synced models
        save_local_models(synced_data)
        
        print("\n✅ Model sync completed successfully!")
        print("\nTo test the sync, run: python3 test_model_routing_fix.py")
        
    except Exception as e:
        print(f"\n❌ Error during sync: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main()) 