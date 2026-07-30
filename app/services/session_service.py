from __future__ import annotations

import json
import secrets
from datetime import datetime, UTC
from typing import Any

from app.core.config import settings
from app.core.redis import get_redis

def generate_session_id() -> str:
    return secrets.token_urlsafe(32)


def session_key(session_id: str) -> str:
    return f"session:{session_id}"


async def create_session(
    *,
    user_id: str,
    email: str,
    organization_id: str,
    roles: list[str],
    permissions: list[str],
    refresh_token_hash: str,
) -> str:
    session_id = generate_session_id()
    session_data = {
        "session_id": session_id,
        "user_id": user_id,
        "email": email,
        "organization_id": organization_id,
        "roles": roles,
        "permissions": permissions,
        "refresh_token_hash": refresh_token_hash,
        "created_at": datetime.now(UTC).isoformat(),
    }
    redis = get_redis()
    await redis.setex(
        session_key(session_id),
        settings.refresh_token_expire_seconds,
        json.dumps(session_data),
    )
    return session_id


async def update_session(
    session_id: str,
    *,
    user_id: str,
    email: str,
    organization_id: str,
    roles: list[str],
    permissions: list[str],
    refresh_token_hash: str,
) -> None:
    session_data = {
        "session_id": session_id,
        "user_id": user_id,
        "email": email,
        "organization_id": organization_id,
        "roles": roles,
        "permissions": permissions,
        "refresh_token_hash": refresh_token_hash,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    redis = get_redis()
    await redis.setex(
        session_key(session_id),
        settings.refresh_token_expire_seconds,
        json.dumps(session_data),
    )


async def get_session(session_id: str) -> dict[str, Any] | None:
    redis = get_redis()

    raw = await redis.get(session_key(session_id))

    if raw is None:
        return None

    return json.loads(raw)

async def delete_session(session_id: str) -> None:
    redis = get_redis()

    await redis.delete(session_key(session_id))

async def touch_session(session_id: str) -> bool:
    redis = get_redis()

    return await redis.expire(
        session_key(session_id),
        settings.refresh_token_expire_seconds,
    )
