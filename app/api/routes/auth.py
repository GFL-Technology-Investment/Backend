from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, status
from pydantic import BaseModel

from app.api.deps.auth import require_internal_auth
from app.core import oidc
from app.core.auth_context import InternalAuthContext
from app.core.config import settings
from app.core.security import (
    create_internal_jwt,
    generate_refresh_token,
    hash_refresh_token,
)
from app.core.status import AUTH_DEV_MODE_DISABLED
from app.database import get_db

router = APIRouter(prefix="/api/v1/auth")


class DevLoginRequest(BaseModel):
    username: str
    password: str


DEV_USERS = {
    "guard@company.com": {
        "user_id": "user-dev-001",
        "password": "123456",
        "email": "guard@company.com",
        "org_id": "org-001",
        "roles": ["guard"],
        "permissions": [
            "ocr.cccd.create", "face.compare", "ticket.issue",
            "ticket.print", "access.checkout", "history.read",
        ],
        "camera": {
            "camera_id": "camera-dev-001",
            "camera_token": settings.dev_camera_token,
            "org_id": "org-001",
            "location_id": "loc-001",
            "gate_id": "gate-001",
        },
    },
    "admin@example.com": {
        "user_id": "user-dev-002",
        "password": "123456",
        "email": "admin@example.com",
        "org_id": "org-001",
        "roles": ["admin"],
        "permissions": ["*"],
    },
}


def _issue_refresh_token(
    db: sqlite3.Connection,
    user_id: str,
    organization_id: str,
) -> str:
    """Tạo refresh token mới, lưu hash vào DB, trả plaintext."""
    raw = generate_refresh_token()
    now_sql = db.execute("SELECT datetime('now') AS n").fetchone()["n"]
    expires_sql = db.execute(
        "SELECT datetime('now', ? ) AS e",
        (f"+{settings.refresh_token_expire_seconds} seconds",),
    ).fetchone()["e"]

    db.execute(
        """
        INSERT INTO refresh_tokens
            (refresh_token_id, user_id, token_hash, organization_id, issued_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            user_id,
            hash_refresh_token(raw),
            organization_id,
            now_sql,
            expires_sql,
        ),
    )
    return raw


def _build_token_response(
    user_id: str,
    email: str,
    org_id: str,
    roles: list,
    permissions: list,
    refresh_token_raw: str,
    extra: Optional[dict] = None,
) -> dict:
    access_token = create_internal_jwt({
        "sub": user_id,
        "email": email,
        "org_id": org_id,
        "roles": roles,
        "permissions": permissions,
    })
    resp = {
        "status": "SUCCESS",
        "token_type": "Bearer",
        "access_token": access_token,
        "refresh_token": refresh_token_raw,
        "expires_in_seconds": settings.internal_jwt_expire_seconds,
        "refresh_expires_in_seconds": settings.refresh_token_expire_seconds,
        "user": {
            "user_id": user_id,
            "email": email,
            "organization_id": org_id,
            "roles": roles,
            "permissions": permissions,
        },
    }
    if extra:
        resp.update(extra)
    return resp


@router.post("/dev-login")
async def dev_login(
    payload: DevLoginRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    if not settings.auth_dev_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": AUTH_DEV_MODE_DISABLED, "message": "AUTH_DEV_MODE=false"},
        )

    user = DEV_USERS.get(payload.username)
    if user is None or user["password"] != payload.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "INVALID_CREDENTIALS", "message": "Sai tài khoản hoặc mật khẩu"},
        )

    refresh_raw = _issue_refresh_token(db, user["user_id"], user["org_id"])
    db.commit()

    return _build_token_response(
        user_id=user["user_id"],
        email=user["email"],
        org_id=user["org_id"],
        roles=user["roles"],
        permissions=user["permissions"],
        refresh_token_raw=refresh_raw,
        extra={"camera": user.get("camera")},
    )


@router.post("/refresh")
async def refresh_access_token(
    refresh_token: str = Form(..., description="Refresh token nhận được khi login"),
    db: sqlite3.Connection = Depends(get_db),
):
    """Đổi refresh token lấy access token mới + refresh token mới (rotation)."""
    token_hash = hash_refresh_token(refresh_token)
    row = db.execute(
        "SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)
    ).fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "INVALID_REFRESH_TOKEN", "message": "Refresh token không hợp lệ"},
        )

    if int(row["is_revoked"] or 0) == 1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "REFRESH_TOKEN_REVOKED", "message": "Refresh token đã bị thu hồi"},
        )

    # Phát hiện reuse: token cũ đã được rotate nhưng vẫn bị dùng lại
    # → dấu hiệu token bị đánh cắp → revoke toàn bộ session của user
    if row["replaced_by"] is not None:
        db.execute(
            "UPDATE refresh_tokens SET is_revoked = 1 WHERE user_id = ?",
            (row["user_id"],),
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "status": "REFRESH_TOKEN_REUSE_DETECTED",
                "message": "Phát hiện tái sử dụng refresh token. Toàn bộ phiên đã bị thu hồi.",
            },
        )

    # Check hết hạn qua SQLite để nhất quán với cách lưu ISO string
    expired = db.execute(
        "SELECT CASE WHEN ? < datetime('now') THEN 1 ELSE 0 END AS expired",
        (row["expires_at"],),
    ).fetchone()["expired"]

    if expired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "REFRESH_TOKEN_EXPIRED", "message": "Refresh token đã hết hạn, vui lòng đăng nhập lại"},
        )

    # Check user vẫn còn active
    user_row = db.execute(
        "SELECT * FROM users WHERE user_id = ? AND is_active = 1", (row["user_id"],)
    ).fetchone()
    if not user_row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "USER_INACTIVE", "message": "Tài khoản không tồn tại hoặc đã bị khóa"},
        )

    # Rotation: tạo refresh token mới, đánh dấu token cũ đã được thay thế
    new_refresh_raw = _issue_refresh_token(db, row["user_id"], row["organization_id"])
    new_hash = hash_refresh_token(new_refresh_raw)
    new_row = db.execute(
        "SELECT refresh_token_id FROM refresh_tokens WHERE token_hash = ?", (new_hash,)
    ).fetchone()

    db.execute(
        "UPDATE refresh_tokens SET replaced_by = ? WHERE refresh_token_id = ?",
        (new_row["refresh_token_id"], row["refresh_token_id"]),
    )
    db.commit()

    roles = json.loads(user_row["roles"] or "[]")
    permissions = json.loads(user_row["permissions"] or "[]")

    return _build_token_response(
        user_id=user_row["user_id"],
        email=user_row["email"],
        org_id=row["organization_id"],
        roles=roles,
        permissions=permissions,
        refresh_token_raw=new_refresh_raw,
    )


@router.post("/logout")
async def logout(
    refresh_token: Optional[str] = Form(None, description="Refresh token cần thu hồi"),
    auth: InternalAuthContext = Depends(require_internal_auth),
    db: sqlite3.Connection = Depends(get_db),
):
    """Thu hồi refresh token. Access token vẫn có hiệu lực đến khi hết exp (chấp nhận được vì đã ngắn 15 phút)."""
    if refresh_token:
        token_hash = hash_refresh_token(refresh_token)
        db.execute(
            "UPDATE refresh_tokens SET is_revoked = 1 WHERE token_hash = ? AND user_id = ?",
            (token_hash, auth.user_id),
        )
        db.commit()

    logout_url = (
        f"{settings.azure_issuer}/protocol/openid-connect/logout"
        f"?post_logout_redirect_uri={settings.frontend_url}/login"
        if settings.azure_issuer
        else None
    )
    return {
        "status": "SUCCESS",
        "message": "Đã đăng xuất thành công",
        "logout_url": logout_url,
    }


@router.get("/me")
async def get_me(auth: InternalAuthContext = Depends(require_internal_auth)):
    return {"status": "SUCCESS", "data": auth.to_dict()}


@router.get("/dev-camera-token")
async def get_dev_camera_token():
    if not settings.auth_dev_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": AUTH_DEV_MODE_DISABLED, "message": "AUTH_DEV_MODE=false"},
        )
    return {
        "status": "SUCCESS",
        "data": {
            "camera_token": settings.dev_camera_token,
            "organization_id": settings.default_organization_id,
            "headers": {
                "Authorization": f"Bearer {settings.dev_camera_token}",
                "X-Organization-ID": settings.default_organization_id,
            },
        },
    }


# ---------------------------------------------------------------------------
# Azure AD exchange (giữ nguyên logic cũ, chỉ thêm refresh token)
# ---------------------------------------------------------------------------

class AzureExchangeRequest(BaseModel):
    id_token: str
    access_token: str
    org_id: Optional[str] = None


@router.post("/azure/exchange")
async def azure_exchange(
    payload: AzureExchangeRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    claims = await oidc.verify_oidc_tokens(
        id_token=payload.id_token,
        access_token=payload.access_token,
    )

    azure_user_id = str(claims.get("sub") or "")
    email = claims.get("email") or claims.get("preferred_username")

    if not azure_user_id or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "AUTH_INVALID_TOKEN", "message": "Token thiếu sub hoặc email"},
        )

    roles = oidc.extract_list_claim(claims, settings.azure_roles_claim) or ["guard"]
    permissions = oidc.extract_list_claim(claims, settings.azure_permissions_claim)
    org_id = (
        payload.org_id
        or oidc.get_claim(claims, settings.azure_org_claim)
        or settings.default_organization_id
    )

    user_row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    if user_row is None:
        user_id = f"azure-{secrets.token_hex(8)}"
        db.execute(
            """
            INSERT INTO users (user_id, email, full_name, organization_id, roles, permissions, azure_user_id, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (user_id, email, claims.get("name", ""), org_id,
             json.dumps(roles), json.dumps(permissions), azure_user_id),
        )
    else:
        if not int(user_row["is_active"] or 0):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"status": "USER_INACTIVE", "message": "Tài khoản đã bị khóa"},
            )
        user_id = user_row["user_id"]
        org_id = user_row["organization_id"]
        roles = json.loads(user_row["roles"] or "[]")
        permissions = json.loads(user_row["permissions"] or "[]")

        if not user_row["azure_user_id"]:
            db.execute(
                "UPDATE users SET azure_user_id = ? WHERE user_id = ?",
                (azure_user_id, user_id),
            )

    refresh_raw = _issue_refresh_token(db, user_id, org_id)
    db.commit()

    return _build_token_response(
        user_id=user_id,
        email=email,
        org_id=org_id,
        roles=roles,
        permissions=permissions,
        refresh_token_raw=refresh_raw,
    )