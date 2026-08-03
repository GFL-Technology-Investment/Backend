from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.services import session_service


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)
        self.ttls.pop(key, None)

    async def expire(self, key, ttl):
        if key not in self.values:
            return False
        self.ttls[key] = ttl
        return True


def _set_redis_enabled(value: bool):
    original = session_service.settings.redis_enabled
    object.__setattr__(session_service.settings, "redis_enabled", value)
    return original


def test_redis_disabled_creates_sqlite_compatible_session_id(monkeypatch):
    original = _set_redis_enabled(False)
    try:
        def fail_if_called():
            raise AssertionError("Redis must not be called when disabled")

        monkeypatch.setattr(session_service, "get_redis", fail_if_called)
        session_id = asyncio.run(
            session_service.create_session(
                user_id="u1",
                email="u1@example.com",
                organization_id="org-001",
                roles=["GUARD"],
                permissions=[],
                refresh_token_hash="hash-1",
            )
        )
        assert session_id
    finally:
        object.__setattr__(session_service.settings, "redis_enabled", original)


def test_session_stores_hash_and_caps_ttl(monkeypatch):
    original = _set_redis_enabled(True)
    fake = FakeRedis()
    try:
        monkeypatch.setattr(session_service, "get_redis", lambda: fake)
        expires = (datetime.now(UTC) + timedelta(seconds=120)).isoformat()
        session_id = asyncio.run(
            session_service.create_session(
                session_id="session-1",
                user_id="u1",
                email="u1@example.com",
                organization_id="org-001",
                roles=["GUARD"],
                permissions=["role.read"],
                refresh_token_hash="hash-1",
                absolute_expires_at=expires,
            )
        )
        session = asyncio.run(session_service.get_session(session_id))
        assert session["refresh_token_hash"] == "hash-1"
        assert session["absolute_expires_at"] == expires
        assert 1 <= fake.ttls["session:session-1"] <= 120
    finally:
        object.__setattr__(session_service.settings, "redis_enabled", original)
