from __future__ import annotations

import logging
from typing import Optional

from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)
redis_client: Optional[Redis] = None


class RedisUnavailableError(RuntimeError):
    """Raised when the configured Redis session store cannot be reached."""


async def init_redis():
    global redis_client

    if not settings.redis_enabled:
        return

    candidate = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await candidate.ping()
    except Exception as exc:
        await candidate.close()
        redis_client = None
        logger.error("Redis session store unavailable during startup")
        return
    redis_client = candidate


async def close_redis():
    global redis_client

    if redis_client:
        await redis_client.close()
        redis_client = None


def get_redis() -> Redis:
    if redis_client is None:
        raise RedisUnavailableError("Redis session store unavailable")
    return redis_client
