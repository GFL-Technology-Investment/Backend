from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Any, Iterable

from app.core.config import settings
from app.core.redis import get_redis


class SessionStoreUnavailable(RuntimeError):
    """Redis is enabled but the session store cannot be used."""


def generate_session_id() -> str:
    return secrets.token_urlsafe(32)


def session_key(session_id: str) -> str:
    return f"session:{session_id}"


def _ttl_seconds(absolute_expires_at: str | None) -> int:
    if not absolute_expires_at:
        return settings.refresh_token_expire_seconds
    try:
        expires = datetime.fromisoformat(absolute_expires_at)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        remaining = int((expires - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        remaining = settings.refresh_token_expire_seconds
    return max(1, min(remaining, settings.refresh_token_expire_seconds))


def _redis_or_none():
    if not settings.redis_enabled:
        return None
    try:
        return get_redis()
    except Exception as exc:
        raise SessionStoreUnavailable("Redis session store unavailable") from exc


def _session_data(
    *,
    session_id: str,
    user_id: str,
    email: str,
    organization_id: str,
    roles: list[str],
    permissions: list[str],
    refresh_token_hash: str,
    absolute_expires_at: str | None,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "user_id": user_id,
        "email": email,
        "organization_id": organization_id,
        "roles": roles,
        "permissions": permissions,
        "refresh_token_hash": refresh_token_hash,
        "absolute_expires_at": absolute_expires_at,
        "created_at": datetime.now(UTC).isoformat(),
    }


async def create_session(
    *,
    user_id: str,
    email: str,
    organization_id: str,
    roles: list[str],
    permissions: list[str],
    refresh_token_hash: str,
    absolute_expires_at: str | None = None,
    session_id: str | None = None,
) -> str:
    session_id = session_id or generate_session_id()
    redis = _redis_or_none()
    if redis is None:
        return session_id
    data = _session_data(
        session_id=session_id,
        user_id=user_id,
        email=email,
        organization_id=organization_id,
        roles=roles,
        permissions=permissions,
        refresh_token_hash=refresh_token_hash,
        absolute_expires_at=absolute_expires_at,
    )
    try:
        await redis.setex(session_key(session_id), _ttl_seconds(absolute_expires_at), json.dumps(data))
    except Exception as exc:
        raise SessionStoreUnavailable("Redis session store unavailable") from exc
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
    absolute_expires_at: str | None = None,
) -> None:
    redis = _redis_or_none()
    if redis is None:
        return
    data = _session_data(
        session_id=session_id,
        user_id=user_id,
        email=email,
        organization_id=organization_id,
        roles=roles,
        permissions=permissions,
        refresh_token_hash=refresh_token_hash,
        absolute_expires_at=absolute_expires_at,
    )
    data["updated_at"] = datetime.now(UTC).isoformat()
    try:
        await redis.setex(session_key(session_id), _ttl_seconds(absolute_expires_at), json.dumps(data))
    except Exception as exc:
        raise SessionStoreUnavailable("Redis session store unavailable") from exc


async def get_session(session_id: str) -> dict[str, Any] | None:
    redis = _redis_or_none()
    if redis is None:
        return None
    try:
        raw = await redis.get(session_key(session_id))
    except Exception as exc:
        raise SessionStoreUnavailable("Redis session store unavailable") from exc
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SessionStoreUnavailable("Invalid Redis session data") from exc


async def delete_session(session_id: str) -> None:
    redis = _redis_or_none()
    if redis is None:
        return
    try:
        await redis.delete(session_key(session_id))
    except Exception as exc:
        raise SessionStoreUnavailable("Redis session store unavailable") from exc


async def delete_sessions(session_ids: Iterable[str]) -> None:
    for session_id in set(session_ids):
        if session_id:
            await delete_session(session_id)


async def touch_session(session_id: str) -> bool:
    session = await get_session(session_id)
    if not session:
        return False
    redis = _redis_or_none()
    if redis is None:
        return False
    try:
        return bool(await redis.expire(session_key(session_id), _ttl_seconds(session.get("absolute_expires_at"))))
    except Exception as exc:
        raise SessionStoreUnavailable("Redis session store unavailable") from exc
