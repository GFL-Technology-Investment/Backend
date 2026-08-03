from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Response
from app.api.deps.auth import require_permission
from app.core.auth_context import InternalAuthContext
from app.core.config import settings
from app.services import login_service, refresh_service, session_service, token_service


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = ttl

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.ttls.pop(key, None)

    async def expire(self, key: str, ttl: int) -> bool:
        if key not in self.values:
            return False
        self.ttls[key] = ttl
        return True


def _set_setting(name: str, value):
    original = getattr(settings, name)
    object.__setattr__(settings, name, value)
    return original


def _refresh_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE refresh_tokens (
            refresh_token_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            session_id TEXT,
            issued_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            is_revoked INTEGER NOT NULL DEFAULT 0,
            replaced_by TEXT,
            rotated_at TEXT
        );
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE user_roles (
            user_id TEXT NOT NULL,
            role_id TEXT NOT NULL
        );
        CREATE TABLE roles (role_id TEXT PRIMARY KEY, role_code TEXT NOT NULL);
        CREATE TABLE role_permissions (role_id TEXT NOT NULL, permission_id TEXT NOT NULL);
        CREATE TABLE permissions (permission_id TEXT PRIMARY KEY, permission_code TEXT NOT NULL);
        """
    )
    db.execute("INSERT INTO users VALUES ('u1', 'u1@example.com', 'org-1', 1)")
    db.commit()
    return db


@pytest.mark.integration
def test_login_without_redis_returns_session_and_refresh_token(monkeypatch):
    original = _set_setting("redis_enabled", False)
    db = _refresh_db()
    try:
        monkeypatch.setattr(session_service, "get_redis", lambda: pytest.fail("Redis was called"))
        result = __import__("asyncio").run(
            login_service.login_user(
                email="guard@company.com",
                password="123456",
                response=Response(),
                db=db,
            )
        )
        assert result["session_id"]
        assert result["access_token"]
        assert db.execute("SELECT COUNT(*) FROM refresh_tokens").fetchone()[0] == 1
    finally:
        db.close()
        object.__setattr__(settings, "redis_enabled", original)


@pytest.mark.integration
def test_redis_session_contains_hash_and_rotation_updates_it(monkeypatch):
    original = _set_setting("redis_enabled", True)
    fake = FakeRedis()
    db = _refresh_db()
    try:
        monkeypatch.setattr(session_service, "get_redis", lambda: fake)
        import asyncio
        raw_refresh, session_id = asyncio.run(
            token_service.issue_refresh_token(
                db=db,
                user_id="u1",
                organization_id="org-1",
                email="u1@example.com",
                roles=[],
                permissions=[],
            )
        )
        session_key = session_service.session_key(session_id)
        first_session = json.loads(fake.values[session_key])
        assert first_session["refresh_token_hash"]

        second = asyncio.run(
            refresh_service.refresh_access_token(
                response=Response(),
                refresh_token=raw_refresh,
                db=db,
            )
        )
        assert second["session_id"] == session_id
        assert json.loads(fake.values[session_key])["refresh_token_hash"] != first_session["refresh_token_hash"]
    finally:
        db.close()
        object.__setattr__(settings, "redis_enabled", original)


@pytest.mark.integration
def test_permission_dependency_allows_exact_permission_and_rejects_missing():
    allowed = require_permission("role.read")
    denied = require_permission("role.delete")
    context = InternalAuthContext(
        user_id="u1",
        email="u1@example.com",
        organization_id="org-1",
        permissions=["role.read"],
    )

    import asyncio
    assert asyncio.run(allowed(context)) is context
    with pytest.raises(Exception) as error:
        asyncio.run(denied(context))
    assert getattr(error.value, "status_code", None) == 403


@pytest.mark.integration
def test_refresh_rejects_redis_hash_mismatch(monkeypatch):
    original = _set_setting("redis_enabled", True)
    fake = FakeRedis()
    db = _refresh_db()
    try:
        monkeypatch.setattr(session_service, "get_redis", lambda: fake)
        raw = "refresh-secret"
        from app.core.security import hash_refresh_token
        token_hash = hash_refresh_token(raw)
        expires = (datetime.now(UTC) + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT INTO refresh_tokens VALUES (?, ?, ?, ?, ?, datetime('now'), ?, 0, NULL, NULL)",
            ("rt-1", "u1", token_hash, "org-1", "session-1", expires),
        )
        db.commit()
        __import__("asyncio").run(
            session_service.create_session(
                session_id="session-1",
                user_id="u1",
                email="u1@example.com",
                organization_id="org-1",
                roles=[],
                permissions=[],
                refresh_token_hash="different-hash",
                absolute_expires_at=expires,
            )
        )
        with pytest.raises(Exception) as error:
            __import__("asyncio").run(
                refresh_service.refresh_access_token(
                    response=Response(),
                    refresh_token=raw,
                    db=db,
                )
            )
        assert getattr(error.value, "status_code", None) == 401
    finally:
        db.close()
        object.__setattr__(settings, "redis_enabled", original)
