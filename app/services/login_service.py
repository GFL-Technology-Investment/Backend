from __future__ import annotations

import logging
import sqlite3

from fastapi import HTTPException, Response, status

from app.core.config import settings
from app.core.security import verify_password
from app.core.status import AUTH_SESSION_STORE_UNAVAILABLE
from app.services.rbac_service import get_user_roles_and_permissions
from app.services.session_service import SessionStoreUnavailable
from app.services.token_service import build_token_response, issue_refresh_token, set_refresh_cookie

logger = logging.getLogger(__name__)


DEV_USERS = {
    "guard@company.com": {
        "user_id": "user-dev-001",
        "password": "123456",
        "email": "guard@company.com",
        "org_id": "org-001",
        "roles": ["ADMIN"],
        "permissions": ["*"],
        "camera": {
            "camera_id": "camera-dev-001",
            "camera_token": settings.dev_camera_token,
            "org_id": "org-001",
            "location_id": "loc-001",
            "gate_id": "gate-001",
        },
    },
    "admin@company.com": {
        "user_id": "user-dev-002",
        "password": "123456",
        "email": "admin@company.com",
        "org_id": "org-001",
        "roles": ["ADMIN"],
        "permissions": ["*"],
        "camera": {
            "camera_id": "camera-dev-001",
            "camera_token": settings.dev_camera_token,
            "org_id": "org-001",
            "location_id": "loc-001",
            "gate_id": "gate-001",
        },
    },
}


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _session_store_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"status": AUTH_SESSION_STORE_UNAVAILABLE, "message": "Session store unavailable"},
    )


async def login_user(
    *,
    email: str,
    password: str,
    response: Response,
    db: sqlite3.Connection,
) -> dict:
    normalized_email = normalize_email(email)

    if settings.auth_dev_mode:
        dev_user = DEV_USERS.get(normalized_email)
        if dev_user:
            if dev_user["password"] != password:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"status": "INVALID_CREDENTIALS", "message": "Invalid email or password"},
                )
            try:
                with db:
                    refresh_raw, session_id = await issue_refresh_token(
                        db=db,
                        user_id=dev_user["user_id"],
                        organization_id=dev_user["org_id"],
                        email=dev_user["email"],
                        roles=dev_user["roles"],
                        permissions=dev_user["permissions"],
                    )
            except SessionStoreUnavailable as exc:
                raise _session_store_error() from exc
            except Exception as exc:
                logger.error("Dev login token issue failed: %s", exc)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={"status": "SERVER_ERROR", "message": "Lỗi hệ thống khi đăng nhập"},
                ) from exc

            set_refresh_cookie(response, refresh_raw)
            return build_token_response(
                user_id=dev_user["user_id"],
                email=dev_user["email"],
                org_id=dev_user["org_id"],
                roles=dev_user["roles"],
                permissions=dev_user["permissions"],
                refresh_token_raw=refresh_raw,
                session_id=session_id,
                extra={
                    "camera": dev_user["camera"],
                    "usage": {
                        "internal_api": "Authorization: Bearer <access_token>",
                        "camera_api": "Authorization: Bearer <camera.camera_token>",
                    },
                },
            )

    user_row = db.execute(
        "SELECT * FROM users WHERE email=? AND is_active=1",
        (normalized_email,),
    ).fetchone()
    if (
        not user_row
        or not user_row["password_hash"]
        or not verify_password(password, user_row["password_hash"])
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "INVALID_CREDENTIALS", "message": "Invalid email or password"},
        )

    roles, permissions = get_user_roles_and_permissions(db, user_row["user_id"])
    try:
        with db:
            refresh_raw, session_id = await issue_refresh_token(
                db=db,
                user_id=user_row["user_id"],
                organization_id=user_row["organization_id"],
                email=user_row["email"],
                roles=roles,
                permissions=permissions,
            )
    except SessionStoreUnavailable as exc:
        raise _session_store_error() from exc
    except Exception as exc:
        logger.error("Login token issue failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "SERVER_ERROR", "message": "Lỗi hệ thống khi đăng nhập"},
        ) from exc

    set_refresh_cookie(response, refresh_raw)
    return build_token_response(
        user_id=user_row["user_id"],
        email=user_row["email"],
        org_id=user_row["organization_id"],
        roles=roles,
        permissions=permissions,
        refresh_token_raw=refresh_raw,
        session_id=session_id,
    )
