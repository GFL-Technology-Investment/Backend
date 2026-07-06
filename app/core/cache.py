from __future__ import annotations

import json
import logging
from typing import Optional

import redis.asyncio as redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: Optional[redis.Redis] = None


def get_redis() -> Optional[redis.Redis]:
    global _redis_client

    if not settings.redis_enabled:
        return None

    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

    return _redis_client


async def cache_get(key: str) -> Optional[dict]:
    client = get_redis()

    if client is None:
        return None

    try:
        raw = await client.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("cache_get failed key=%s err=%s", key, exc)
        return None


async def cache_set(key: str, value: dict, ttl_seconds: int) -> None:
    client = get_redis()

    if client is None:
        return

    try:
        await client.set(key, json.dumps(value), ex=ttl_seconds)
    except Exception as exc:
        logger.warning("cache_set failed key=%s err=%s", key, exc)


async def cache_delete(*keys: str) -> None:
    if not keys:
        return

    client = get_redis()

    if client is None:
        return

    try:
        await client.delete(*keys)
    except Exception as exc:
        logger.warning("cache_delete failed keys=%s err=%s", keys, exc)