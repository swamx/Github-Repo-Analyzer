import json
import logging
from typing import Any, Optional

import redis

from app.config import settings

logger = logging.getLogger(__name__)


class CacheService:
    """Redis-backed cache with in-memory fallback."""

    def __init__(self):
        self.redis_client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            decode_responses=True,
        )
        self._memory_store: dict = {}

    def get(self, key: str) -> Optional[Any]:
        try:
            value = self.redis_client.get(key)
            if value is not None:
                logger.debug("cache hit key=%s source=redis", key)
                return json.loads(value)
        except Exception as e:
            logger.warning("Redis get failed key=%s, falling back to memory: %s", key, e)

        value = self._memory_store.get(key)
        if value is not None:
            logger.debug("cache hit key=%s source=memory", key)
        else:
            logger.debug("cache miss key=%s", key)
        return value

    def set(self, key: str, value: Any) -> None:
        payload = json.dumps(value)
        try:
            self.redis_client.setex(key, settings.CACHE_TTL_SECONDS, payload)
            logger.debug("cache set key=%s ttl=%d source=redis", key, settings.CACHE_TTL_SECONDS)
        except Exception as e:
            logger.warning("Redis set failed key=%s, storing in memory: %s", key, e)
            self._memory_store[key] = value
