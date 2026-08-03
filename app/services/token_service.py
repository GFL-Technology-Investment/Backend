from __future__ import annotations

import sqlite3
import uuid
from typing import Optional

from fastapi import Response

from app.core.config import settings
from app.core.security import create_internal_jwt, generate_refresh_token, hash_refresh_token
from app.services.session_service import SessionStoreUnavailable, create_session, generate_session_id


def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=settings.refresh_token_expire_seconds,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path=settings.refresh_cookie_path,
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path=settings.refresh_cookie_path,
    )


async def issue_refresh_token(
    db: sqlite3.Connection,
    user_id: str,
    organization_id: str,
    email: str,
    roles: list,
    permissions: list,
) -> tuple[str, str]:
    raw = generate_refresh_token()
    refresh_hash = hash_refresh_token(raw)
    session_id = generate_session_id()
    now_sql = db.execute("SELECT datetime('now') AS n").fetchone()["n"]
    expires_sql = db.execute(
        "SELECT datetime('now', ? ) AS e",
        (f"+{settings.refresh_token_expire_seconds} seconds",),
    ).fetchone()["e"]
    refresh_token_id = str(uuid.uuid4())

    db.execute(
        """
        INSERT INTO refresh_tokens
        (
            refresh_token_id,
            user_id,
            token_hash,
            organization_id,
            session_id,
            issued_at,
            expires_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            refresh_token_id,
            user_id,
            refresh_hash,
            organization_id,
            session_id,
            now_sql,
            expires_sql,
        ),
    )
    try:
        await create_session(
            user_id=user_id,
            email=email,
            organization_id=organization_id,
            roles=roles,
            permissions=permissions,
            refresh_token_hash=refresh_hash,
            absolute_expires_at=expires_sql,
            session_id=session_id,
        )
    except SessionStoreUnavailable:
        db.execute("UPDATE refresh_tokens SET is_revoked = 1 WHERE refresh_token_id = ?", (refresh_token_id,))
        db.commit()
        raise
    return raw, session_id


def build_token_response(
    user_id: str,
    email: str,
    org_id: str,
    roles: list,
    permissions: list,
    refresh_token_raw: str,
    session_id: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    access_token = create_internal_jwt({
        "sub": user_id,
        "email": email,
        "org_id": org_id,
        "roles": roles,
        "permissions": permissions,
        "session_id": session_id,
    })
    response = {
        "status": "SUCCESS",
        "token_type": "Bearer",
        "access_token": access_token,
        "expires_in_seconds": settings.internal_jwt_expire_seconds,
        "refresh_expires_in_seconds": settings.refresh_token_expire_seconds,
        "session_id": session_id,
        "user": {
            "user_id": user_id,
            "email": email,
            "organization_id": org_id,
            "roles": roles,
            "permissions": permissions,
        },
    }
    if extra:
        response.update(extra)
    return response
