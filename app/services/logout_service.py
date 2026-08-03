from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from fastapi import HTTPException, Response, status

from app.core import oidc
from app.core.auth_context import InternalAuthContext
from app.core.config import settings
from app.core.status import AUTH_SESSION_STORE_UNAVAILABLE
from app.core.security import hash_refresh_token
from app.services.session_service import SessionStoreUnavailable, delete_session
from app.services.token_service import clear_refresh_cookie

logger = logging.getLogger(__name__)


async def logout_user(
    *,
    response: Response,
    provider: Optional[str],
    refresh_token: Optional[str],
    auth: InternalAuthContext,
    db: sqlite3.Connection,
) -> dict:
    session_id = auth.session_id
    if refresh_token:
        token_hash = hash_refresh_token(refresh_token)
        row = db.execute(
            "SELECT session_id FROM refresh_tokens WHERE token_hash = ? AND user_id = ?",
            (token_hash, auth.user_id),
        ).fetchone()
        if row and row["session_id"]:
            session_id = row["session_id"]
        db.execute(
            "UPDATE refresh_tokens SET is_revoked = 1 WHERE token_hash = ? AND user_id = ?",
            (token_hash, auth.user_id),
        )
    elif session_id:
        db.execute(
            "UPDATE refresh_tokens SET is_revoked = 1 WHERE session_id = ? AND user_id = ?",
            (session_id, auth.user_id),
        )

    if session_id:
        try:
            await delete_session(session_id)
        except SessionStoreUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": AUTH_SESSION_STORE_UNAVAILABLE, "message": "Session store unavailable"},
            ) from exc

    if refresh_token or session_id:
        db.commit()

    logout_url = None
    if provider:
        try:
            discovery = await oidc.get_discovery(provider)
            end_session_endpoint = discovery.get("end_session_endpoint")
            if end_session_endpoint:
                logout_url = f"{end_session_endpoint}?post_logout_redirect_uri={settings.frontend_url}/login"
        except Exception as exc:
            logger.warning("Could not resolve OIDC logout endpoint for provider=%s: %s", provider, exc)

    clear_refresh_cookie(response)
    return {
        "status": "SUCCESS",
        "message": "Đã đăng xuất thành công",
        "logout_url": logout_url,
    }
