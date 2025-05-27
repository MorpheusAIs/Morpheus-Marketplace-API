"""
Automatic model synchronization module.
Handles syncing local models.json with active models from the API.
"""

import json
import os
import asyncio
import logging
from typing import Dict, List, Optional
import httpx
from datetime import datetime, timedelta

# Import settings
from src.core.config import settings

logger = logging.getLogger(__name__)

LOCAL_MODELS_FILE = "models.json"

class ModelSyncService:
    """Service for automatically syncing model data."""
    
    def __init__(self, auto_sync_on_startup: bool = None, sync_interval_hours: Optional[int] = None):
        """
        Initialize the model sync service.
        
        Args:
            auto_sync_on_startup: Whether to sync models on application startup (uses config if None)
            sync_interval_hours: If set, will sync models every N hours (uses config if None)
        """
        self.auto_sync_on_startup = auto_sync_on_startup if auto_sync_on_startup is not None else settings.MODEL_SYNC_ON_STARTUP
        self.sync_interval_hours = sync_interval_hours if sync_interval_hours is not None else (settings.MODEL_SYNC_INTERVAL_HOURS if settings.MODEL_SYNC_ENABLED else None)
        self.last_sync_time: Optional[datetime] = None
        self._sync_task: Optional[asyncio.Task] = None
        
    async def fetch_active_models(self) -> List[Dict]:
        """Fetch active models from the API endpoint."""
        logger.info(f"Fetching active models from {settings.ACTIVE_MODELS_URL}")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(settings.ACTIVE_MODELS_URL, timeout=10.0)
                response.raise_for_status()
                
                data = response.json()
                models = data.get("models", [])
                logger.info(f"Successfully fetched {len(models)} active models")
                return models
        except Exception as e:
            logger.error(f"Failed to fetch active models: {e}")
            raise
    
    def load_local_models(self) -> Dict:
        """Load the local models.json file."""
        models_path = self._get_models_file_path()
        
        if not os.path.exists(models_path):
            logger.warning(f"Local models file {models_path} not found, creating empty structure")
            return {"models": []}
        
        try:
            with open(models_path, 'r') as f:
                data = json.load(f)
            
            logger.info(f"Loaded {len(data.get('models', []))} models from local file")
            return data
        except Exception as e:
            logger.error(f"Failed to load local models file: {e}")
            return {"models": []}
    
    def save_local_models(self, models_data: Dict):
        """Save the models data to the local models.json file."""
        models_path = self._get_models_file_path()
        
        try:
            # Create backup of existing file
            if os.path.exists(models_path):
                backup_path = f"{models_path}.backup"
                os.rename(models_path, backup_path)
                logger.info(f"Created backup at {backup_path}")
            
            with open(models_path, 'w') as f:
                json.dump(models_data, f, indent=2)
            
            logger.info(f"Successfully saved {len(models_data.get('models', []))} models to {models_path}")
            
        except Exception as e:
            logger.error(f"Failed to save models file: {e}")
            # Restore backup if save failed
            backup_path = f"{models_path}.backup"
            if os.path.exists(backup_path):
                os.rename(backup_path, models_path)
                logger.info("Restored backup due to save failure")
            raise
    
    def _get_models_file_path(self) -> str:
        """Get the path to the models.json file."""
        # Get the project root directory (where models.json should be)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        return os.path.join(project_root, LOCAL_MODELS_FILE)
    
    def sync_models(self, active_models: List[Dict], local_data: Dict) -> Dict:
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
        local_by_id = {model["Id"]: model for model in local_models}
        
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
        
        # Log summary
        logger.info("Model sync summary:")
        logger.info(f"  ✅ Added models ({len(added_models)}): {', '.join(added_models) if added_models else 'None'}")
        logger.info(f"  🔄 Updated models ({len(updated_models)}): {', '.join(updated_models) if updated_models else 'None'}")
        logger.info(f"  📌 Kept unchanged ({len(kept_models)}): {len(kept_models)} models")
        logger.info(f"  🏠 Local-only models ({len(local_only_models)}): {len(local_only_models)} models")
        
        return {"models": synced_models}
    
    async def perform_sync(self) -> bool:
        """
        Perform a complete model sync.
        
        Returns:
            bool: True if sync was successful, False otherwise
        """
        try:
            logger.info("Starting model sync...")
            
            # Fetch active models
            active_models = await self.fetch_active_models()
            
            # Load local models
            local_data = self.load_local_models()
            
            # Sync models
            synced_data = self.sync_models(active_models, local_data)
            
            # Save synced models
            self.save_local_models(synced_data)
            
            self.last_sync_time = datetime.now()
            logger.info("✅ Model sync completed successfully!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Model sync failed: {e}")
            return False
    
    async def start_background_sync(self):
        """Start the background sync task if periodic sync is enabled."""
        if self.sync_interval_hours is None:
            logger.info("Periodic model sync is disabled")
            return
        
        logger.info(f"Starting background model sync (every {self.sync_interval_hours} hours)")
        self._sync_task = asyncio.create_task(self._background_sync_loop())
    
    async def stop_background_sync(self):
        """Stop the background sync task."""
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            logger.info("Background model sync stopped")
    
    async def _background_sync_loop(self):
        """Background loop for periodic model syncing."""
        while True:
            try:
                # Wait for the sync interval
                await asyncio.sleep(self.sync_interval_hours * 3600)  # Convert hours to seconds
                
                logger.info("Performing scheduled model sync...")
                await self.perform_sync()
                
            except asyncio.CancelledError:
                logger.info("Background sync loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in background sync loop: {e}")
                # Continue the loop even if sync fails
                await asyncio.sleep(300)  # Wait 5 minutes before retrying

# Global instance
model_sync_service = ModelSyncService() 