"""
Direct model fetching service with in-memory cache and hash optimization.
Replaces the complex model sync system with a simple, efficient approach.
"""

import json
import hashlib
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import httpx

from src.core.config import settings
from src.core.logging_config import get_models_logger

logger = get_models_logger()

class DirectModelService:
    """
    Service for fetching model data directly from CloudFront with intelligent caching.
    
    Features:
    - In-memory cache with configurable TTL (default 5 minutes)
    - Hash-based cache invalidation to avoid unnecessary downloads
    - Automatic fallback to cache on network errors
    - Optimized for ECS container memory usage
    """
    
    def __init__(self, cache_duration_seconds: int = 300):
        """
        Initialize the direct model service.
        
        Args:
            cache_duration_seconds: Cache TTL in seconds (default: 300 = 5 minutes)
        """
        self.cache_duration = cache_duration_seconds
        self._model_mapping: Dict[str, str] = {}  # name -> blockchain_id
        self._blockchain_ids: set = set()
        self._cache_expiry: Optional[datetime] = None
        self._last_etag: Optional[str] = None
        self._last_hash: Optional[str] = None
        self._raw_models_data: List[Dict] = []
        
        logger.info("DirectModelService initialized",
                   cache_duration_seconds=cache_duration_seconds,
                   event_type="model_service_init")
    
    async def get_model_mapping(self) -> Dict[str, str]:
        """
        Get the model name to blockchain ID mapping.
        
        Returns:
            Dict mapping model names to blockchain IDs
        """
        await self._ensure_fresh_cache()
        return self._model_mapping.copy()
    
    async def get_blockchain_ids(self) -> set:
        """
        Get all valid blockchain IDs.
        
        Returns:
            Set of all blockchain IDs
        """
        await self._ensure_fresh_cache()
        return self._blockchain_ids.copy()
    
    async def get_raw_models_data(self) -> List[Dict]:
        """
        Get the raw models data from the API.
        
        Returns:
            List of model dictionaries with all fields
        """
        await self._ensure_fresh_cache()
        return self._raw_models_data.copy()
    
    async def resolve_model_id(self, model_identifier: str) -> Optional[str]:
        """
        Resolve a model name or blockchain ID to a blockchain ID.
        
        Args:
            model_identifier: Either a model name or blockchain ID
            
        Returns:
            Blockchain ID if found, None otherwise
        """
        await self._ensure_fresh_cache()
        
        # Check if it's already a blockchain ID
        if model_identifier in self._blockchain_ids:
            return model_identifier
        
        # Check if it's a model name
        return self._model_mapping.get(model_identifier)
    
    async def _ensure_fresh_cache(self):
        """Ensure the cache is fresh, refresh if needed."""
        now = datetime.now()
        
        if (self._cache_expiry is None or now > self._cache_expiry):
            logger.debug("Cache expired, refreshing model data",
                        cache_expiry=self._cache_expiry.isoformat() if self._cache_expiry else None,
                        event_type="cache_refresh")
            await self._refresh_cache()
        else:
            cache_remaining = (self._cache_expiry - now).total_seconds()
            logger.debug("Using cached model data",
                        cache_expires_in_seconds=cache_remaining,
                        event_type="cache_hit")
    
    async def _refresh_cache(self):
        """Refresh the cache by fetching from the API."""
        try:
            logger.info("Fetching models from external API",
                   source_url=settings.ACTIVE_MODELS_URL,
                   event_type="external_models_fetch_start")
            
            headers = {}
            if self._last_etag:
                headers['If-None-Match'] = self._last_etag
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    settings.ACTIVE_MODELS_URL,
                    headers=headers,
                    timeout=10.0
                )
                
                # Handle 304 Not Modified
                if response.status_code == 304:
                    logger.info("Models data unchanged (304 Not Modified), extending cache")
                    self._extend_cache()
                    return
                
                response.raise_for_status()
                
                # Get response data and hash
                response_text = response.text
                current_hash = hashlib.sha256(response_text.encode()).hexdigest()
                
                # Check if content actually changed (hash comparison)
                if current_hash == self._last_hash:
                    logger.info("Models data unchanged (same hash), extending cache")
                    self._extend_cache()
                    return
                
                # Parse new data
                data = response.json()
                models = data.get("models", [])
                
                # Update cache
                self._update_cache(models, current_hash, response.headers.get('ETag'))
                
                logger.info(f"✅ Successfully refreshed {len(models)} models")
                
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching models: {e}")
            if self._model_mapping:
                logger.warning("Using stale cache due to HTTP error")
                self._extend_cache()
            else:
                raise
        except Exception as e:
            logger.error(f"Error fetching models: {e}")
            if self._model_mapping:
                logger.warning("Using stale cache due to error")
                self._extend_cache()
            else:
                raise
    
    def _update_cache(self, models: List[Dict], content_hash: str, etag: Optional[str]):
        """Update the internal cache with new model data."""
        # Build mappings
        new_mapping = {}
        new_blockchain_ids = set()
        
        for model in models:
            if model.get("IsDeleted", False):
                continue
                
            model_name = model.get("Name")
            blockchain_id = model.get("Id")
            
            if model_name and blockchain_id:
                new_mapping[model_name] = blockchain_id
                new_blockchain_ids.add(blockchain_id)
        
        # Update cache
        self._model_mapping = new_mapping
        self._blockchain_ids = new_blockchain_ids
        self._raw_models_data = models
        self._last_hash = content_hash
        self._last_etag = etag
        self._cache_expiry = datetime.now() + timedelta(seconds=self.cache_duration)
        
        logger.info(f"Cache updated: {len(new_mapping)} model mappings, {len(new_blockchain_ids)} blockchain IDs")
    
    def _extend_cache(self):
        """Extend the current cache expiry without changing data."""
        self._cache_expiry = datetime.now() + timedelta(seconds=self.cache_duration)
        logger.debug(f"Cache extended for {self.cache_duration} seconds")
    
    def get_cache_stats(self) -> Dict:
        """Get cache statistics for monitoring."""
        now = datetime.now()
        return {
            "cached_models": len(self._model_mapping),
            "cached_blockchain_ids": len(self._blockchain_ids),
            "cache_expiry": self._cache_expiry.isoformat() if self._cache_expiry else None,
            "seconds_until_expiry": (self._cache_expiry - now).total_seconds() if self._cache_expiry else None,
            "last_hash": self._last_hash,
            "last_etag": self._last_etag,
            "cache_duration": self.cache_duration
        }

# Global instance
direct_model_service = DirectModelService()
