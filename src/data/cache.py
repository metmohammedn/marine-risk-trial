"""
Redis cache layer for the Weather Intelligence Dashboard.
All cached data flows through this module for consistency.
"""
import logging
import pickle
from typing import Any, Optional

import redis

logger = logging.getLogger(__name__)


class CacheManager:
    """Redis-backed cache with pickle serialization."""

    def __init__(self, redis_url: str):
        self._redis = redis.from_url(
            redis_url,
            decode_responses=False,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )
        self._available = True
        try:
            self._redis.ping()
            logger.info("Redis cache connected: %s", redis_url)
        except redis.ConnectionError:
            self._available = False
            logger.warning(
                "Redis not available at %s — running without cache", redis_url
            )

    @property
    def is_available(self) -> bool:
        return self._available

    def get(self, key: str) -> Optional[Any]:
        """Retrieve a cached value. Returns None on miss or error."""
        if not self._available:
            return None
        try:
            data = self._redis.get(key)
            if data is not None:
                return pickle.loads(data)
            return None
        except (redis.RedisError, pickle.UnpicklingError) as e:
            logger.warning("Cache get error for key=%s: %s", key, e)
            return None

    def set(self, key: str, value: Any, ttl_seconds: int = 3600) -> bool:
        """Store a value in cache with TTL."""
        if not self._available:
            return False
        try:
            serialized = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
            self._redis.setex(key, ttl_seconds, serialized)
            return True
        except (redis.RedisError, pickle.PicklingError) as e:
            logger.warning("Cache set error for key=%s: %s", key, e)
            return False

    def delete(self, key: str) -> bool:
        """Delete a single key."""
        if not self._available:
            return False
        try:
            self._redis.delete(key)
            return True
        except redis.RedisError as e:
            logger.warning("Cache delete error for key=%s: %s", key, e)
            return False

    def invalidate_pattern(self, pattern: str) -> int:
        """Delete all keys matching a glob pattern. Returns count deleted."""
        if not self._available:
            return 0
        try:
            count = 0
            for key in self._redis.scan_iter(match=pattern, count=100):
                self._redis.delete(key)
                count += 1
            return count
        except redis.RedisError as e:
            logger.warning("Cache invalidate error for pattern=%s: %s", pattern, e)
            return 0

    def get_or_set(self, key: str, factory, ttl_seconds: int = 3600) -> Any:
        """Get from cache, or call factory() to compute + cache the result."""
        cached = self.get(key)
        if cached is not None:
            return cached
        value = factory()
        self.set(key, value, ttl_seconds)
        return value


# Module-level singleton (initialized in app factory)
_cache: Optional[CacheManager] = None


def init_cache(redis_url: str) -> CacheManager:
    """Initialize the global cache manager."""
    global _cache
    _cache = CacheManager(redis_url)
    return _cache


def get_cache() -> CacheManager:
    """Get the global cache manager."""
    if _cache is None:
        raise RuntimeError("Cache not initialized. Call init_cache() first.")
    return _cache
