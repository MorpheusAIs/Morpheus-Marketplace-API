#!/usr/bin/env python
import json
import os
import re

def extract_model_id(full_id):
    """
    Extracts the model ID from the full model name that includes blockchain ID.
    For example, from "LMR-OpenAI-GPT-4o [ID:0x8f9f631f...]" extracts "LMR-OpenAI-GPT-4o".
    """
    return full_id.split(" [ID:")[0] if " [ID:" in full_id else full_id

def extract_blockchain_id(full_id):
    """
    Extracts the blockchain ID from the full model name.
    For example, from "LMR-OpenAI-GPT-4o [ID:0x8f9f631f...]" extracts "0x8f9f631f...".
    """
    # Use regex to extract the ID inside [ID:...]
    match = re.search(r'\[ID:(0x[a-f0-9]+)\]', full_id)
    if match:
        return match.group(1)
    return None

def generate_model_mappings():
    """
    Generates model_mappings.json from models.json with 1:1 mappings.
    Uses the first model in the list as the default.
    """
    # Paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    models_json_path = os.path.join(project_root, 'models.json')
    output_path = os.path.join(project_root, 'config', 'model_mappings.json')
    
    # Ensure config directory exists
    os.makedirs(os.path.join(project_root, 'config'), exist_ok=True)
    
    # Load models data
    with open(models_json_path, 'r') as f:
        models_data = json.load(f)
    
    # Build mappings
    mappings = {}
    default_model = None
    
    for model in models_data.get('data', []):
        model_id = extract_model_id(model.get('id', ''))
        blockchain_id = extract_blockchain_id(model.get('id', ''))
        
        if model_id and blockchain_id:
            # Store the model mapping
            mappings[model_id] = blockchain_id
            
            # Set first model as default if not set
            if default_model is None:
                default_model = blockchain_id
    
    # Set default model
    if default_model:
        mappings['default'] = default_model
    
    # Add standard OpenAI model names as an example (can be edited manually)
    openai_models = {
        'gpt-3.5-turbo': None,
        'gpt-4': None,
        'gpt-4o': None, 
        'claude-3-opus': None
    }
    
    # Try to find matches for common model names
    for openai_model, _ in openai_models.items():
        for model_name in mappings.keys():
            if openai_model.lower() in model_name.lower():
                openai_models[openai_model] = mappings[model_name]
                break
    
    # Add OpenAI models with matched blockchain IDs or None
    for openai_model, blockchain_id in openai_models.items():
        if blockchain_id is not None:
            mappings[openai_model] = blockchain_id
    
    # Write to file
    with open(output_path, 'w') as f:
        json.dump(mappings, f, indent=2)
    
    print(f"Generated model_mappings.json with {len(mappings)} mappings.")
    print(f"File saved to: {output_path}")
    return mappings

if __name__ == "__main__":
    mappings = generate_model_mappings()
    print("\nModel mappings:")
    for model, blockchain_id in mappings.items():
        print(f"{model}: {blockchain_id}") 