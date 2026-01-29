"""
Redis Cache Manager for AWH Station Monitoring API
Handles caching of station data and readings for improved performance
"""

import redis
import json
from typing import Optional, Any
from datetime import timedelta
import logging

from config import settings

logger = logging.getLogger(__name__)


class RedisCache:
    """Redis cache manager with connection pooling and error handling"""
    
    def __init__(self):
        self.enabled = False
        self.client = None
        self._connect()
    
    def _connect(self):
        """Establish Redis connection with error handling"""
        try:
            self.client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password if settings.redis_password else None,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30
            )
            # Test connection
            self.client.ping()
            self.enabled = True
            logger.info(f"✅ Redis connected: {settings.redis_host}:{settings.redis_port}")
        except redis.ConnectionError as e:
            logger.warning(f"⚠️  Redis connection failed: {e}. Continuing without cache.")
            self.enabled = False
        except Exception as e:
            logger.warning(f"⚠️  Redis error: {e}. Continuing without cache.")
            self.enabled = False
    
    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/error
        """
        if not self.enabled:
            return None
        
        try:
            value = self.client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Redis GET error for key '{key}': {e}")
            return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Set value in cache with optional TTL
        
        Args:
            key: Cache key
            value: Value to cache (will be JSON serialized)
            ttl: Time to live in seconds (defaults to settings.cache_ttl)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
        
        try:
            ttl = ttl or settings.cache_ttl
            serialized = json.dumps(value, default=str)
            self.client.setex(key, ttl, serialized)
            return True
        except Exception as e:
            logger.error(f"Redis SET error for key '{key}': {e}")
            return False
    
    def delete(self, key: str) -> bool:
        """
        Delete key from cache
        
        Args:
            key: Cache key to delete
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
        
        try:
            self.client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Redis DELETE error for key '{key}': {e}")
            return False
    
    def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern
        
        Args:
            pattern: Pattern to match (e.g., "stations:*")
            
        Returns:
            Number of keys deleted
        """
        if not self.enabled:
            return 0
        
        try:
            keys = self.client.keys(pattern)
            if keys:
                return self.client.delete(*keys)
            return 0
        except Exception as e:
            logger.error(f"Redis DELETE PATTERN error for '{pattern}': {e}")
            return 0
    
    def flush_all(self) -> bool:
        """
        Flush all cache data (use with caution!)
        
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
        
        try:
            self.client.flushdb()
            logger.info("🗑️  Redis cache flushed")
            return True
        except Exception as e:
            logger.error(f"Redis FLUSH error: {e}")
            return False
    
    def get_stats(self) -> dict:
        """
        Get Redis cache statistics
        
        Returns:
            Dictionary with cache stats
        """
        if not self.enabled:
            return {
                "enabled": False,
                "status": "disabled"
            }
        
        try:
            info = self.client.info()
            return {
                "enabled": True,
                "status": "connected",
                "used_memory": info.get("used_memory_human", "N/A"),
                "connected_clients": info.get("connected_clients", 0),
                "total_keys": self.client.dbsize(),
                "uptime_seconds": info.get("uptime_in_seconds", 0)
            }
        except Exception as e:
            logger.error(f"Redis STATS error: {e}")
            return {
                "enabled": False,
                "status": "error",
                "error": str(e)
            }
    
    def health_check(self) -> bool:
        """
        Check if Redis is healthy
        
        Returns:
            True if healthy, False otherwise
        """
        if not self.enabled:
            return False
        
        try:
            return self.client.ping()
        except:
            return False


# Global cache instance
cache = RedisCache()


# Cache key generators
def get_stations_cache_key() -> str:
    """Get cache key for stations list"""
    return "stations:all"


def get_station_readings_cache_key(station_name: str, limit: int, offset: int, 
                                   start_date: Optional[str] = None, 
                                   end_date: Optional[str] = None,
                                   fields: Optional[str] = None) -> str:
    """Get cache key for station readings with query parameters"""
    key_parts = [
        "readings",
        station_name,
        f"limit={limit}",
        f"offset={offset}"
    ]
    
    if start_date:
        key_parts.append(f"start={start_date}")
    if end_date:
        key_parts.append(f"end={end_date}")
    if fields:
        key_parts.append(f"fields={fields}")
    
    return ":".join(key_parts)


def invalidate_station_cache(station_name: Optional[str] = None):
    """
    Invalidate cache for a station or all stations
    
    Args:
        station_name: Station to invalidate, or None for all stations
    """
    if station_name:
        # Delete specific station readings
        cache.delete_pattern(f"readings:{station_name}:*")
        logger.info(f"♻️  Invalidated cache for station: {station_name}")
    else:
        # Delete all station-related caches
        cache.delete_pattern("stations:*")
        cache.delete_pattern("readings:*")
        logger.info("♻️  Invalidated all station caches")
