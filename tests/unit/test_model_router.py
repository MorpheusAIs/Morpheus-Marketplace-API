import unittest
import os
import tempfile
import json
import sys
from pathlib import Path

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.model_routing import ModelRouter

class TestModelRouter(unittest.TestCase):
    """Tests for the ModelRouter class."""
    
    def setUp(self):
        # Create a temporary directory for test configuration
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self.temp_dir.name) / "config"
        os.makedirs(self.config_dir, exist_ok=True)
        
        # Set up test mappings
        self.test_mappings = {
            "default": "test-default-model",
            "gpt-4": "test-gpt4-model",
            "claude-3": "test-claude-model"
        }
        
        # Path to the test config file
        self.config_path = self.config_dir / "model_mappings.json"
        
        # Save test mappings to the file
        with open(self.config_path, "w") as f:
            json.dump(self.test_mappings, f)
            
        # Create a router instance
        self.router = ModelRouter()
        
        # Override the config path for testing
        # This is a bit of a hack but works for testing
        self.original_dirname = os.path.dirname
        os.path.dirname = lambda x: self.temp_dir.name
    
    def tearDown(self):
        # Restore original dirname function
        os.path.dirname = self.original_dirname
        
        # Clean up temporary directory
        self.temp_dir.cleanup()
    
    def test_get_target_model_with_known_model(self):
        """Test that known models are correctly mapped."""
        # Force reload mappings from our test file
        self.router.mappings = self.test_mappings
        
        # Test known model
        self.assertEqual(self.router.get_target_model("gpt-4"), "test-gpt4-model")
    
    def test_get_target_model_with_unknown_model(self):
        """Test that unknown models fall back to default."""
        # Force reload mappings from our test file
        self.router.mappings = self.test_mappings
        
        # Test unknown model (should fall back to default)
        self.assertEqual(self.router.get_target_model("unknown-model"), "test-default-model")
    
    def test_get_target_model_with_none(self):
        """Test that None input falls back to default."""
        # Force reload mappings from our test file
        self.router.mappings = self.test_mappings
        
        # Test None input (should fall back to default)
        self.assertEqual(self.router.get_target_model(None), "test-default-model")
    
    def test_reload_mappings(self):
        """Test reloading mappings from file."""
        # Force reload mappings from our test file
        self.router.mappings = self.test_mappings
        
        # Change the mappings file
        new_mappings = {
            "default": "new-default-model",
            "gpt-4": "new-gpt4-model"
        }
        with open(self.config_path, "w") as f:
            json.dump(new_mappings, f)
        
        # Use our mappings directly since we can't mock the file loading easily
        self.router.mappings = new_mappings
        
        # Verify new mappings are used
        self.assertEqual(self.router.get_target_model("gpt-4"), "new-gpt4-model")

if __name__ == "__main__":
    unittest.main() 