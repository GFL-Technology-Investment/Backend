from __future__ import annotations

import logging
import sqlite3
import uuid

from fastapi import HTTPException, Response, status

from app.core.config import settings
from app.core.security import generate_refresh_token, hash_refresh_token
from app.core.status import AUTH_SESSION_STORE_UNAVAILABLE
from app.services.rbac_service import get_user_roles_and_permissions
from app.services.session_service import SessionStoreUnavailable, create_session, delete_session, generate_session_id, get_session, update_session
from app.services.token_service import build_token_response, set_refresh_cookie

logger = logging.getLogger(__name__)


def _session_store_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"status": AUTH_SESSION_STORE_UNAVAILABLE, "message": "Session store unavailable"},
    )


async def refresh_access_token(
    *,
    response: Response,
    refresh_token: str,
    db: sqlite3.Connection,
) -> dict:
    current_hash = hash_refresh_token(refresh_token)

    for _hop in range(3):
        new_refresh_id = str(uuid.uuid4())
        db.execute("BEGIN TRANSACTION")
        try:
            claim = db.execute(
                """
                UPDATE refresh_tokens
                SET replaced_by = ?, rotated_at = datetime('now')
                WHERE token_hash = ?
                  AND is_revoked = 0
                  AND replaced_by IS NULL
                  AND expires_at > datetime('now')
                """,
                (new_refresh_id, current_hash),
            )

            if claim.rowcount == 1:
                row = db.execute("SELECT * FROM refresh_tokens WHERE token_hash = ?", (current_hash,)).fetchone()
                user_row = db.execute(
                    "SELECT * FROM users WHERE user_id = ? AND is_active = 1", (row["user_id"],)
                ).fetchone()
                if not user_row:
                    db.execute("ROLLBACK")
                    db.execute("UPDATE refresh_tokens SET is_revoked = 1 WHERE token_hash = ?", (current_hash,))
                    db.commit()
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail={"status": "USER_INACTIVE", "message": "Tài khoản không tồn tại hoặc đã bị khóa"},
                    )

                roles, permissions = get_user_roles_and_permissions(db, user_row["user_id"])
                session_id = row["session_id"]
                existing_session = None

                if session_id and settings.redis_enabled:
                    existing_session = await get_session(session_id)
                    if not existing_session:
                        db.execute("ROLLBACK")
                        db.execute("UPDATE refresh_tokens SET is_revoked = 1 WHERE token_hash = ?", (current_hash,))
                        db.commit()
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={"status": "SESSION_REVOKED", "message": "Phiên đăng nhập đã hết hạn hoặc bị thu hồi"},
                        )
                    if existing_session.get("refresh_token_hash") != current_hash:
                        db.execute("ROLLBACK")
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={"status": "INVALID_REFRESH_TOKEN", "message": "Refresh token không hợp lệ"},
                        )
                elif not session_id:
                    session_id = generate_session_id()

                new_raw = generate_refresh_token()
                new_hash = hash_refresh_token(new_raw)
                now_sql = db.execute("SELECT datetime('now') AS n").fetchone()["n"]
                expires_sql = db.execute(
                    "SELECT datetime('now', ?) AS e",
                    (f"+{settings.refresh_token_expire_seconds} seconds",),
                ).fetchone()["e"]

                db.execute(
                    """
                    INSERT INTO refresh_tokens
                        (refresh_token_id, user_id, token_hash, organization_id, session_id, issued_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (new_refresh_id, row["user_id"], new_hash, row["organization_id"], session_id, now_sql, expires_sql),
                )
                db.commit()

                try:
                    if existing_session:
                        await update_session(
                            session_id,
                            user_id=user_row["user_id"],
                            email=user_row["email"],
                            organization_id=row["organization_id"],
                            roles=roles,
                            permissions=permissions,
                            refresh_token_hash=new_hash,
                            absolute_expires_at=expires_sql,
                        )
                    else:
                        await create_session(
                            session_id=session_id,
                            user_id=user_row["user_id"],
                            email=user_row["email"],
                            organization_id=row["organization_id"],
                            roles=roles,
                            permissions=permissions,
                            refresh_token_hash=new_hash,
                            absolute_expires_at=expires_sql,
                        )
                except SessionStoreUnavailable as exc:
                    db.execute("UPDATE refresh_tokens SET is_revoked = 1 WHERE refresh_token_id = ?", (new_refresh_id,))
                    db.commit()
                    try:
                        await delete_session(session_id)
                    except SessionStoreUnavailable:
                        logger.error("Redis session cleanup failed after SQLite commit")
                    logger.error("Redis session update failed after SQLite commit")
                    raise _session_store_error() from exc

                set_refresh_cookie(response, new_raw)
                return build_token_response(
                    user_id=user_row["user_id"],
                    email=user_row["email"],
                    org_id=row["organization_id"],
                    roles=roles,
                    permissions=permissions,
                    refresh_token_raw=new_raw,
                    session_id=session_id,
                )

            db.execute("ROLLBACK")
            row = db.execute("SELECT * FROM refresh_tokens WHERE token_hash = ?", (current_hash,)).fetchone()
            if not row:
                logger.warning("Refresh token not found in DB")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"status": "INVALID_REFRESH_TOKEN", "message": "Refresh token không hợp lệ"},
                )
            if int(row["is_revoked"] or 0) == 1:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"status": "REFRESH_TOKEN_REVOKED", "message": "Refresh token đã bị thu hồi"},
                )

            expired = db.execute(
                "SELECT CASE WHEN ? <= datetime('now') THEN 1 ELSE 0 END AS expired",
                (row["expires_at"],),
            ).fetchone()["expired"]
            if expired:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"status": "REFRESH_TOKEN_EXPIRED", "message": "Refresh token đã hết hạn, vui lòng đăng nhập lại"},
                )

            if row["replaced_by"] is not None:
                within_grace = db.execute(
                    """
                    SELECT CASE
                        WHEN ? IS NULL THEN 0
                        WHEN (julianday('now') - julianday(?)) * 86400 <= ? THEN 1
                        ELSE 0
                    END AS within_grace
                    """,
                    (row["rotated_at"], row["rotated_at"], settings.refresh_token_rotation_grace_seconds),
                ).fetchone()["within_grace"]
                if within_grace:
                    successor = db.execute(
                        "SELECT token_hash FROM refresh_tokens WHERE refresh_token_id = ?",
                        (row["replaced_by"],),
                    ).fetchone()
                    if successor:
                        logger.info("Refresh race condition detected; following successor")
                        current_hash = successor["token_hash"]
                        continue

                db.execute("UPDATE refresh_tokens SET is_revoked = 1 WHERE session_id = ?", (row["session_id"],))
                db.commit()
                if row["session_id"]:
                    try:
                        await delete_session(row["session_id"])
                    except SessionStoreUnavailable as exc:
                        raise _session_store_error() from exc
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "status": "REFRESH_TOKEN_REUSE_DETECTED",
                        "message": "Phát hiện tái sử dụng refresh token. Phiên đăng nhập đã bị thu hồi.",
                    },
                )

        except HTTPException:
            raise
        except SessionStoreUnavailable as exc:
            db.execute("ROLLBACK")
            logger.error("Redis session store unavailable during refresh")
            raise _session_store_error() from exc
        except Exception as exc:
            db.execute("ROLLBACK")
            logger.error("Refresh token processing failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"status": "SERVER_ERROR", "message": "Lỗi hệ thống khi làm mới token"},
            ) from exc

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"status": "REFRESH_CONFLICT", "message": "Quá nhiều request refresh đồng thời, vui lòng thử lại sau giây lát"},
    )
