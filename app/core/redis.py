from __future__ import annotations

from typing import Optional

from redis.asyncio import Redis

from app.core.config import settings

redis_client: Optional[Redis] = None


async def init_redis():
    global redis_client

    if not settings.redis_enabled:
        return

    redis_client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
    )

    await redis_client.ping()


async def close_redis():
    global redis_client

    if redis_client:
        await redis_client.close()
        redis_client = None


def get_redis() -> Redis:
    if redis_client is None:
        raise RuntimeError("Redis chưa khởi tạo")
    return redis_client