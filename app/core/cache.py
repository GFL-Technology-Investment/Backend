
from __future__ import annotations

import json
import logging
from typing import Optional

import redis.asyncio as redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


async def cache_get(key: str) -> Optional[dict]:
    try:
        raw = await get_redis().get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("cache_get failed key=%s err=%s", key, exc)
        return None


async def cache_set(key: str, value: dict, ttl_seconds: int) -> None:
    try:
        await get_redis().set(key, json.dumps(value), ex=ttl_seconds)
    except Exception as exc:
        logger.warning("cache_set failed key=%s err=%s", key, exc)


async def cache_delete(*keys: str) -> None:
    if not keys:
        return
    try:
        await get_redis().delete(*keys)
    except Exception as exc:
        logger.warning("cache_delete failed keys=%s err=%s", keys, exc)