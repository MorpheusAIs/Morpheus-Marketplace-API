import asyncio
import logging
from typing import List

from .model_mapper import model_mapper
from .redis_client import redis_client
from ..schemas import openai as openai_schemas
from ..core.config import settings

logger = logging.getLogger(__name__)


async def init_model_cache():
    """
    Initialize the models cache in Redis.
    
    This function should be called during application startup to ensure
    that the Redis cache contains initial model data.
    """
    try:
        # Force refresh of all model caches
        await model_mapper.refresh_all_caches()
        logger.info("Model cache initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize model cache: {e}")


def test_redis_connection():
    """
    Test the Redis connection.
    
    This is useful during application startup to ensure Redis is available.
    """
    try:
        logger.info(f"Testing Redis connection using URL: {settings.REDIS_URL} (obscured password)")
        
        # Set a test key
        redis_client.set("test:connection", "ok", expire=60)
        logger.info("Successfully set test key in Redis")
        
        # Verify we can read it back
        value = redis_client.get("test:connection")
        if value != "ok":
            logger.error(f"Redis test failed: expected 'ok', got '{value}'")
            return False
            
        # Verify we can delete the key
        redis_client.delete("test:connection")
        logger.info("Successfully deleted test key from Redis")
        
        logger.info("Redis connection test passed ✓")
        return True
    except Exception as e:
        logger.error(f"Redis connection test failed: {e}")
        return False 