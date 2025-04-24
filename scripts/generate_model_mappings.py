#!/usr/bin/env python
import json
import os
import re
import sys
import subprocess
from collections import OrderedDict

def fetch_blockchain_models():
    """
    Attempts to fetch models data directly from the blockchain using proxy-router.
    Returns the JSON response from the blockchain or None if failed.
    """
    try:
        # Change to the Morpheus-Lumerin-Node directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        node_dir = os.path.join(project_root, 'Morpheus-Lumerin-Node')
        proxy_router = os.path.join(node_dir, 'proxy-router', 'proxy-router')
        
        if not os.path.exists(node_dir):
            print("Warning: Morpheus-Lumerin-Node directory not found")
            return None
            
        if not os.path.exists(proxy_router):
            print("Warning: proxy-router executable not found")
            return None
            
        # Run the proxy-router command to get models
        result = subprocess.run(
            [proxy_router, 'models'],
            cwd=os.path.join(node_dir, 'proxy-router'),
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            print(f"Warning: Error fetching models from blockchain: {result.stderr}")
            return None
            
        # Save the output to models.json
        models_data = json.loads(result.stdout)
        models_json_path = os.path.join(project_root, 'models.json')
        with open(models_json_path, 'w') as f:
            json.dump(models_data, f, indent=2)
            
        print("Successfully fetched and updated models from blockchain")
        return models_data
        
    except Exception as e:
        print(f"Warning: Error fetching blockchain models: {str(e)}")
        return None

def load_local_models():
    """
    Loads models data from the local models.json file.
    """
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        models_json_path = os.path.join(project_root, 'models.json')
        
        if not os.path.exists(models_json_path):
            print(f"Error: models.json not found at {models_json_path}")
            return None
            
        with open(models_json_path, 'r') as f:
            return json.load(f)
            
    except Exception as e:
        print(f"Error loading local models.json: {str(e)}")
        return None

def extract_model_id(model_data):
    """
    Extracts the model name from the model data.
    """
    return model_data.get('Name', '')

def extract_blockchain_id(model_data):
    """
    Extracts the blockchain ID from the model data.
    """
    return model_data.get('Id', '')

def generate_model_mappings():
    """
    Generates model_mappings.json from blockchain data or local models.json.
    - First attempts to fetch from blockchain
    - Falls back to local models.json if blockchain fetch fails
    - Maintains order and appends new models to the bottom
    - Updates existing models' blockchain IDs
    - Sets deleted models to use default blockchain ID
    """
    # Paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    output_path = os.path.join(project_root, 'config', 'model_mappings.json')
    
    # Ensure config directory exists
    os.makedirs(os.path.join(project_root, 'config'), exist_ok=True)
    
    # Try to fetch from blockchain first
    models_data = fetch_blockchain_models()
    
    # If blockchain fetch fails, try local file
    if not models_data:
        print("Falling back to local models.json...")
        models_data = load_local_models()
        if not models_data:
            print("Error: Could not load models from either blockchain or local file")
            return

    # Load existing mappings to preserve order
    existing_mappings = {}
    if os.path.exists(output_path):
        with open(output_path, 'r') as f:
            existing_mappings = json.load(f)

    # Track changes
    new_models = []
    updated_models = set()  # Changed to a set to track unique updates
    deleted_models = []

    # Handle both possible data structures
    models_list = models_data.get('models', []) or models_data.get('data', [])
    
    for model in models_list:
        model_id = extract_model_id(model)
        blockchain_id = extract_blockchain_id(model)
        is_deleted = model.get('IsDeleted', False)
        
        if model_id and blockchain_id:
            # Set first active model as default if not set
            if 'default' not in existing_mappings and not is_deleted:
                existing_mappings['default'] = blockchain_id
            
            if is_deleted:
                # For deleted models, set to use default blockchain ID
                if model_id in existing_mappings and existing_mappings[model_id] != 'default':
                    existing_mappings[model_id] = 'default'
                    deleted_models.append(model_id)
            else:
                # For active models, update or add
                if model_id in existing_mappings:
                    if existing_mappings[model_id] != blockchain_id:
                        existing_mappings[model_id] = blockchain_id
                        updated_models.add(model_id)
                else:
                    existing_mappings[model_id] = blockchain_id
                    new_models.append(model_id)
    
    # Add standard OpenAI model names if not present
    openai_models = {
        'gpt-3.5-turbo': None,
        'gpt-4': None,
        'gpt-4o': None, 
        'claude-3-opus': None
    }
    
    # Try to find matches for common model names
    for openai_model, _ in openai_models.items():
        if openai_model not in existing_mappings:  # Only process if not already in mappings
            for model_name in existing_mappings.keys():
                if openai_model.lower() in model_name.lower():
                    openai_models[openai_model] = existing_mappings[model_name]
                    break
    
    # Add OpenAI models with matched blockchain IDs or None
    for openai_model, blockchain_id in openai_models.items():
        if blockchain_id is not None and openai_model not in existing_mappings:
            existing_mappings[openai_model] = blockchain_id
            new_models.append(openai_model)
    
    # Write to file
    with open(output_path, 'w') as f:
        json.dump(existing_mappings, f, indent=2)
    
    # Print summary
    print("\nModel mappings updated:")
    print(f"- New models added ({len(new_models)}): {', '.join(new_models) if new_models else 'None'}")
    print(f"- Models updated ({len(updated_models)}): {', '.join(updated_models) if updated_models else 'None'}")
    print(f"- Deleted models ({len(deleted_models)}): {', '.join(deleted_models) if deleted_models else 'None'}")
    print(f"\nFile saved to: {output_path}")
    
    return existing_mappings

if __name__ == "__main__":
    mappings = generate_model_mappings()
    if mappings:
        print("\nFinal model mappings:")
        for model, blockchain_id in mappings.items():
            print(f"{model}: {blockchain_id}") 