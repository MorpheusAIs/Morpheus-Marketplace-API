import json
import redis
from typing import Any, Dict, List, Optional, Union, TypeVar, Generic, Type
import time
from datetime import timedelta
from pydantic import BaseModel
from urllib.parse import urlparse

from ..core.config import settings

T = TypeVar('T', bound=BaseModel)

class RedisClient:
    """
    Redis client for caching operations.
    
    Handles connection to Redis and provides methods for caching operations
    with proper serialization/deserialization of Pydantic models.
    """
    
    def __init__(self):
        """Initialize Redis connection from settings."""
        # Parse Redis URL from settings
        redis_url = str(settings.REDIS_URL)
        parsed_url = urlparse(redis_url)
        
        # Extract host, port, password from URL
        host = parsed_url.hostname or 'localhost'
        port = parsed_url.port or 6379
        password = None
        
        # Password could be in userinfo (redis://:password@host) or
        # in the netloc without a username (redis://password@host)
        if parsed_url.password:
            password = parsed_url.password
        elif parsed_url.username:
            # If no password but there's a username, it might be the password
            # (common pattern in Redis URLs without auth username)
            password = parsed_url.username
        
        # If password still not found in URL, use the one from settings
        if not password and hasattr(settings, 'REDIS_PASSWORD'):
            password = settings.REDIS_PASSWORD
        
        print(f"Connecting to Redis at {host}:{port} with {'password' if password else 'no password'}")
        
        # Create Redis connection
        self.redis = redis.Redis(
            host=host,
            port=port,
            password=password,
            decode_responses=False,  # We'll handle decoding ourselves
            socket_timeout=5,  # 5 second timeout
            socket_connect_timeout=5,
            retry_on_timeout=True
        )
        
        # Test connection at startup
        try:
            self.redis.ping()
        except redis.ConnectionError as e:
            # In production, you might want to log this rather than raise
            # to allow the application to start even if Redis is temporarily down
            print(f"Warning: Redis connection failed: {e}")
    
    def _serialize(self, value: Any) -> bytes:
        """Serialize a value to JSON bytes."""
        if isinstance(value, BaseModel):
            return json.dumps(value.dict()).encode('utf-8')
        return json.dumps(value).encode('utf-8')
    
    def _deserialize(self, value: bytes, model_type: Optional[Type[T]] = None) -> Any:
        """Deserialize JSON bytes to a value or model."""
        if value is None:
            return None
        
        data = json.loads(value.decode('utf-8'))
        if model_type:
            return model_type.parse_obj(data)
        return data
    
    def get(self, key: str, model_type: Optional[Type[T]] = None) -> Optional[Union[Any, T]]:
        """
        Get a value from Redis.
        
        Args:
            key: Redis key
            model_type: Optional Pydantic model type for deserialization
            
        Returns:
            The deserialized value or None if key doesn't exist
        """
        value = self.redis.get(key)
        if value is None:
            return None
        return self._deserialize(value, model_type)
    
    def set(
        self, 
        key: str, 
        value: Any, 
        expire: Optional[Union[int, timedelta]] = None
    ) -> bool:
        """
        Set a value in Redis with optional expiration.
        
        Args:
            key: Redis key
            value: Value to cache (will be JSON serialized)
            expire: Optional expiration time in seconds or as timedelta
            
        Returns:
            True if successful
        """
        serialized = self._serialize(value)
        
        if expire is not None:
            if isinstance(expire, timedelta):
                expire = int(expire.total_seconds())
            return self.redis.setex(key, expire, serialized)
        else:
            return self.redis.set(key, serialized)
    
    def delete(self, key: str) -> int:
        """
        Delete a key from Redis.
        
        Args:
            key: Redis key
            
        Returns:
            Number of keys deleted (0 or 1)
        """
        return self.redis.delete(key)
    
    def exists(self, key: str) -> bool:
        """
        Check if a key exists in Redis.
        
        Args:
            key: Redis key
            
        Returns:
            True if key exists
        """
        return bool(self.redis.exists(key))
    
    def hash_set(self, hash_key: str, field: str, value: Any) -> int:
        """
        Set a field in a Redis hash.
        
        Args:
            hash_key: Redis hash key
            field: Hash field
            value: Value to cache (will be JSON serialized)
            
        Returns:
            1 if field is new, 0 if field was updated
        """
        serialized = self._serialize(value)
        return self.redis.hset(hash_key, field, serialized)
    
    def hash_get(
        self, 
        hash_key: str, 
        field: str, 
        model_type: Optional[Type[T]] = None
    ) -> Optional[Union[Any, T]]:
        """
        Get a field from a Redis hash.
        
        Args:
            hash_key: Redis hash key
            field: Hash field
            model_type: Optional Pydantic model type for deserialization
            
        Returns:
            The deserialized value or None if field doesn't exist
        """
        value = self.redis.hget(hash_key, field)
        if value is None:
            return None
        return self._deserialize(value, model_type)
    
    def hash_get_all(
        self, 
        hash_key: str, 
        model_type: Optional[Type[T]] = None
    ) -> Dict[str, Union[Any, T]]:
        """
        Get all fields and values from a Redis hash.
        
        Args:
            hash_key: Redis hash key
            model_type: Optional Pydantic model type for deserialization
            
        Returns:
            Dict of field names to deserialized values
        """
        values = self.redis.hgetall(hash_key)
        if not values:
            return {}
        
        return {
            k.decode('utf-8'): self._deserialize(v, model_type)
            for k, v in values.items()
        }


# Create a singleton instance to be used throughout the application
redis_client = RedisClient() 