"""
Direct model fetching service with in-memory cache and hash optimization.
Replaces the complex model sync system with a simple, efficient approach.
"""

import hashlib
import re
from collections import defaultdict
from datetime import datetime, timedelta
from difflib import get_close_matches
from typing import Dict, List, Optional, Set

import httpx

from src.core.config import settings
from src.core.logging_config import get_models_logger

logger = get_models_logger()

# Client suffixes that often appear on otherwise-valid catalog names.
# Stripped only when looking for near-miss suggestions (not for exact resolve).
_NEAR_MISS_STRIP_SUFFIXES = (
    "-turbo",
    "-instruct-turbo",
)


def catalog_name_slug(name: str) -> str:
    """Deterministic kebab slug of a catalog Name (spaces/underscores → '-').

    Keeps dots and feature suffixes like ':web'. Not fuzzy — just a
    predictable form of the on-chain/display name for API clients.
    """
    s = str(name or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


def _alias_candidates(model_name: str, enrichment: Optional[dict]) -> Set[str]:
    """Extra lowercase resolve keys for a catalog model (excluding the name itself)."""
    aliases: Set[str] = set()
    name_key = model_name.lower()

    slug = catalog_name_slug(model_name)
    if slug and slug != name_key:
        aliases.add(slug)

    venice_id = (enrichment or {}).get("veniceId") or (enrichment or {}).get("venice_id")
    if isinstance(venice_id, str):
        vid = venice_id.strip().lower()
        if vid and vid != name_key:
            aliases.add(vid)

    return aliases


def _strip_near_miss_suffixes(slug: str) -> List[str]:
    """Return progressively stripped variants of a request slug."""
    out: List[str] = []
    current = slug
    changed = True
    while changed:
        changed = False
        for suffix in _NEAR_MISS_STRIP_SUFFIXES:
            if current.endswith(suffix) and len(current) > len(suffix):
                current = current[: -len(suffix)]
                out.append(current)
                changed = True
                break
    return out


def suggest_near_miss_models(
    requested: str,
    catalog_keys: Set[str],
    id_to_name: Dict[str, str],
    model_mapping: Dict[str, str],
    *,
    limit: int = 5,
) -> List[str]:
    """Suggest active catalog Names for a near-miss client string.

    Strategies (in order):
      1. Exact hit after stripping known junk suffixes (e.g. ``-turbo``)
      2. difflib close matches against resolve keys (slug/spaced/venice)
    Returns unique catalog display Names (not alias keys).
    """
    if not requested or not catalog_keys:
        return []

    def _display_name(resolve_key: str) -> Optional[str]:
        blockchain_id = model_mapping.get(resolve_key)
        if not blockchain_id:
            return None
        return id_to_name.get(blockchain_id)

    slug = catalog_name_slug(requested)
    candidates: List[str] = []

    for variant in _strip_near_miss_suffixes(slug):
        name = _display_name(variant)
        if name and name not in candidates:
            candidates.append(name)

    if len(candidates) >= limit:
        return candidates[:limit]

    pool = sorted(catalog_keys)
    needles = [slug, requested.lower(), *_strip_near_miss_suffixes(slug)]
    for needle in dict.fromkeys(n for n in needles if n):
        for hit in get_close_matches(needle, pool, n=limit, cutoff=0.72):
            name = _display_name(hit)
            if name and name not in candidates:
                candidates.append(name)
            if len(candidates) >= limit:
                return candidates[:limit]

    return candidates[:limit]


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
        self._model_mapping: Dict[str, str] = {}  # lowercase name -> blockchain_id
        self._id_to_name: Dict[str, str] = {}  # blockchain_id -> name
        self._model_mapping_type: Dict[str, str] = {}  # lowercase name -> type
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

    async def get_model_mapping_type(self) -> Dict[str, str]:
        """
        Get the model name to type mapping.
        
        Returns:
            Dict mapping model names to types
        """
        await self._ensure_fresh_cache()
        return self._model_mapping_type.copy()
    
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

        key = model_identifier.lower()
        hit = self._model_mapping.get(key)
        if hit:
            return hit

        # Request-side slug: clients often send spaced Title Case
        # ("Llama 3.2 3B") for kebab catalog names ("llama-3.2-3b").
        # Cache-time aliases only go catalog→slug; this is the reverse.
        slug = catalog_name_slug(model_identifier)
        if slug and slug != key:
            return self._model_mapping.get(slug)
        return None

    async def suggest_models(self, requested: str, *, limit: int = 5) -> List[str]:
        """Return near-miss catalog Names for a requested model string."""
        await self._ensure_fresh_cache()
        return suggest_near_miss_models(
            requested,
            set(self._model_mapping.keys()),
            self._id_to_name,
            self._model_mapping,
            limit=limit,
        )
    
    async def get_model_name_from_id(self, blockchain_id: str) -> Optional[str]:
        """
        Reverse-lookup: get the model name for a given blockchain ID.
        O(1) via pre-built reverse mapping.
        
        Args:
            blockchain_id: The blockchain ID to look up
            
        Returns:
            Model name if found, None otherwise
        """
        await self._ensure_fresh_cache()
        return self._id_to_name.get(blockchain_id)
    
    async def _ensure_fresh_cache(self):
        """Ensure the cache is fresh, refresh if needed."""
        now = datetime.now()
        
        if (self._cache_expiry is None or now > self._cache_expiry):
            if self._cache_expiry is None:
                logger.debug("Cache miss, fetching model data for first time",
                            event_type="cache_miss")
            else:
                logger.debug("Cache expired, refreshing model data",
                            cache_expiry=self._cache_expiry.isoformat(),
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
        """Update the internal cache with new model data.

        Resolve keys (all lowercase):
          1. Catalog Name (authoritative — never overwritten)
          2. enrichment.veniceId when unique
          3. kebab slug of catalog Name when unique and distinct from (1)
        Colliding aliases are skipped so we never guess between two models.
        """
        new_mapping: Dict[str, str] = {}
        new_id_to_name: Dict[str, str] = {}
        new_mapping_type: Dict[str, str] = {}
        new_blockchain_ids: set = set()
        alias_claims: Dict[str, Set[str]] = defaultdict(set)

        for model in models:
            if model.get("IsDeleted", False):
                continue

            model_name = model.get("Name")
            blockchain_id = model.get("Id")
            model_type = model.get("ModelType")

            if not model_name or not blockchain_id:
                continue

            name_key = model_name.lower()
            new_mapping[name_key] = blockchain_id
            new_id_to_name[blockchain_id] = model_name
            new_mapping_type[name_key] = model_type
            new_blockchain_ids.add(blockchain_id)

            for alias in _alias_candidates(model_name, model.get("enrichment")):
                if alias in new_mapping:
                    # Catalog name (or earlier authoritative key) wins — never override.
                    continue
                alias_claims[alias].add(blockchain_id)

        aliases_added = 0
        aliases_skipped_collision = 0
        for alias, ids in alias_claims.items():
            if alias in new_mapping:
                continue
            if len(ids) != 1:
                aliases_skipped_collision += 1
                logger.info(
                    "Skipping ambiguous model alias",
                    alias=alias,
                    claimant_count=len(ids),
                    event_type="model_alias_collision",
                )
                continue
            blockchain_id = next(iter(ids))
            new_mapping[alias] = blockchain_id
            # Type lookups for aliases use id→catalog name; no type entry needed.
            aliases_added += 1

        self._model_mapping = new_mapping
        self._id_to_name = new_id_to_name
        self._model_mapping_type = new_mapping_type
        self._blockchain_ids = new_blockchain_ids
        self._raw_models_data = models
        self._last_hash = content_hash
        self._last_etag = etag
        self._cache_expiry = datetime.now() + timedelta(seconds=self.cache_duration)

        logger.info(
            "Cache updated",
            model_mappings=len(new_mapping),
            blockchain_ids=len(new_blockchain_ids),
            aliases_added=aliases_added,
            aliases_skipped_collision=aliases_skipped_collision,
            event_type="model_cache_updated",
        )
    
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
