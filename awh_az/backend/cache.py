"""
Redis Cache Manager for AWH Station Monitoring API
Handles caching of station data and readings for improved performance

Falls back to an in-process cache when Redis is unreachable, so callers of
get()/set() still get real caching (just not shared across instances)
instead of silently becoming a permanent no-op. See
ingestion_checkpoint_desync_recurrence_2026-08-17 memory / the 2026-08-17
session for why this mattered here: /hourly and /readings were correctly
wired to cache.get()/cache.set() all along, but Redis was never actually
reachable in this deployment, so every request recomputed from Firestore.
"""

import fnmatch
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Optional, Any

import redis

from config import settings

logger = logging.getLogger(__name__)


class _InMemoryCache:
    """Thread-safe, bounded (oldest-evicted-first) fallback cache.

    Only covers a single process — fine for a single Render instance, but
    won't be shared across multiple instances/workers the way real Redis
    would be. Values are stored pre-serialized (JSON strings) so the
    get/set contract matches RedisCache's client exactly.
    """

    def __init__(self, max_entries: int = 500):
        self._store: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, serialized = entry
            if time.time() >= expires_at:
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return serialized

    def set(self, key: str, serialized: str, ttl: int) -> None:
        with self._lock:
            self._store[key] = (time.time() + ttl, serialized)
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def delete_pattern(self, pattern: str) -> int:
        with self._lock:
            keys = [k for k in self._store if fnmatch.fnmatch(k, pattern)]
            for k in keys:
                del self._store[k]
            return len(keys)

    def flush_all(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


class RedisCache:
    """Redis cache manager with connection pooling and error handling.

    Transparently falls back to an in-process cache (see _InMemoryCache)
    whenever Redis is unavailable, so `enabled` reflects true Redis
    connectivity (used for honest /health reporting) while get()/set()
    still function via the fallback.
    """

    def __init__(self):
        self.enabled = False
        self.client = None
        self._memory = _InMemoryCache()
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
        Get value from cache (Redis if connected, else the in-process fallback)

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/error
        """
        if self.enabled:
            try:
                value = self.client.get(key)
                if value:
                    return json.loads(value)
                return None
            except Exception as e:
                logger.error(f"Redis GET error for key '{key}': {e}. Falling back to in-process cache.")

        value = self._memory.get(key)
        return json.loads(value) if value else None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Set value in cache with optional TTL (Redis if connected, else the
        in-process fallback)

        Args:
            key: Cache key
            value: Value to cache (will be JSON serialized)
            ttl: Time to live in seconds (defaults to settings.cache_ttl)

        Returns:
            True if successful, False otherwise
        """
        ttl = ttl or settings.cache_ttl
        serialized = json.dumps(value, default=str)

        if self.enabled:
            try:
                self.client.setex(key, ttl, serialized)
                return True
            except Exception as e:
                logger.error(f"Redis SET error for key '{key}': {e}. Falling back to in-process cache.")

        self._memory.set(key, serialized, ttl)
        return True

    def delete(self, key: str) -> bool:
        """
        Delete key from cache (Redis if connected, else the in-process fallback)

        Args:
            key: Cache key to delete

        Returns:
            True if successful, False otherwise
        """
        if self.enabled:
            try:
                self.client.delete(key)
                return True
            except Exception as e:
                logger.error(f"Redis DELETE error for key '{key}': {e}. Falling back to in-process cache.")

        self._memory.delete(key)
        return True

    def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern (Redis if connected, else the
        in-process fallback)

        Args:
            pattern: Pattern to match (e.g., "stations:*")

        Returns:
            Number of keys deleted
        """
        if self.enabled:
            try:
                keys = self.client.keys(pattern)
                if keys:
                    return self.client.delete(*keys)
                return 0
            except Exception as e:
                logger.error(f"Redis DELETE PATTERN error for '{pattern}': {e}. Falling back to in-process cache.")

        return self._memory.delete_pattern(pattern)

    def flush_all(self) -> bool:
        """
        Flush all cache data — both Redis (if connected) and the in-process
        fallback (use with caution!)

        Returns:
            True if successful, False otherwise
        """
        ok = True
        if self.enabled:
            try:
                self.client.flushdb()
                logger.info("🗑️  Redis cache flushed")
            except Exception as e:
                logger.error(f"Redis FLUSH error: {e}")
                ok = False

        self._memory.flush_all()
        return ok

    def get_stats(self) -> dict:
        """
        Get cache statistics, covering both Redis (if connected) and the
        in-process fallback

        Returns:
            Dictionary with cache stats
        """
        stats = {
            "redis_enabled": self.enabled,
            "fallback_keys": len(self._memory),
        }

        if not self.enabled:
            stats["status"] = "using in-process fallback cache (Redis unreachable)"
            return stats

        try:
            info = self.client.info()
            stats.update({
                "status": "connected",
                "used_memory": info.get("used_memory_human", "N/A"),
                "connected_clients": info.get("connected_clients", 0),
                "total_keys": self.client.dbsize(),
                "uptime_seconds": info.get("uptime_in_seconds", 0),
            })
        except Exception as e:
            logger.error(f"Redis STATS error: {e}")
            stats["status"] = f"error: {e}"

        return stats
    
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
