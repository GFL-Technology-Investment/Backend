"""FastAPI auth dependencies.

- require_internal_auth: dùng cho API nội bộ FE/user.
- require_camera_auth: dùng cho Camera/AIBox/service, bắt buộc Bearer token + X-Organization-ID.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from fastapi import Depends, Header, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth_context import CameraAuthContext, InternalAuthContext
from app.core.config import settings
from app.core.security import AuthError, decode_internal_jwt, extract_bearer_token, hash_camera_token
from app.core.status import (
    AUTH_INVALID_TOKEN,
    ORG_HEADER_REQUIRED,
    ORG_MISMATCH,
    PERMISSION_DENIED,
)
from app.database import get_db


bearer_scheme = HTTPBearer(auto_error=False)


def _token_from_credentials(credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    """Return raw token from Swagger/Postman Authorization: Bearer header.

    Dùng HTTPBearer để Swagger UI gửi header Authorization chuẩn qua nút Authorize.
    """
    if credentials is None:
        return None
    if (credentials.scheme or "").lower() != "bearer":
        return None
    return credentials.credentials


def _json_list(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except Exception:
        pass
    return [item.strip() for item in str(value).split(",") if item.strip()]


async def require_internal_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    db: sqlite3.Connection = Depends(get_db),
) -> InternalAuthContext:
    """Verify JWT nội bộ và inject InternalAuthContext vào request.state."""
    print("========== AUTH ==========")
    print("Headers:", dict(request.headers))
    print("Credentials:", credentials)
    print("==========================")

    if not settings.auth_enabled:
        context = InternalAuthContext(
            user_id="auth-disabled",
            email="auth-disabled@local",
            organization_id=settings.default_organization_id,
            roles=["dev"],
            permissions=["*"],
        )
        request.state.internal_auth = context
        return context

    raw_token = _token_from_credentials(credentials)
    if raw_token is None:
        # fallback để vẫn hỗ trợ client gửi header thủ công trong một số proxy/test tool
        raw_token = extract_bearer_token(request.headers.get("Authorization"))
    print("1. Before decode")
    payload = decode_internal_jwt(raw_token)
    print("2. After decode")
    print(payload)
    user_id = str(payload.get("sub") or payload.get("user_id") or "")
    print("3. user_id =", user_id)
    email = str(payload.get("email") or "")
    organization_id = str(payload.get("org_id") or payload.get("organization_id") or "")
    roles = [str(item) for item in payload.get("roles", [])]
    permissions = [str(item) for item in payload.get("permissions", [])]

    if not user_id or not email or not organization_id:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Internal token missing required claims")

    row = db.execute("SELECT * FROM users WHERE user_id = ? AND is_active = 1", (user_id,)).fetchone()
    print("4. DB row =", row)
    if not row:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Internal user not found or inactive")

    org_row = db.execute("SELECT * FROM organizations WHERE organization_id = ? AND is_active = 1", (organization_id,)).fetchone()
    if not org_row:
        raise AuthError(status.HTTP_403_FORBIDDEN, ORG_MISMATCH, "Organization not found or inactive")

    context = InternalAuthContext(
        user_id=user_id,
        email=email,
        organization_id=organization_id,
        roles=roles,
        permissions=permissions,
    )
    request.state.internal_auth = context
    return context


def require_permission(permission_code: str):
    async def dependency(context: InternalAuthContext = Depends(require_internal_auth)) -> InternalAuthContext:
        if not context.has_permission(permission_code):
            raise AuthError(
                status.HTTP_403_FORBIDDEN,
                PERMISSION_DENIED,
                f"Permission required: {permission_code}",
            )
        return context

    return dependency


async def require_camera_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    x_organization_id: Optional[str] = Header(None, alias="X-Organization-ID"),
    db: sqlite3.Connection = Depends(get_db),
) -> CameraAuthContext:
    """Verify camera/service token và organization scope.

    Token xác định camera/service là ai.
    X-Organization-ID xác định request muốn ghi dữ liệu vào tổ chức nào.
    Backend phải check token đó có quyền với organization đó không.
    """

    if not settings.auth_enabled:
        context = CameraAuthContext(
            camera_client_id="auth-disabled-camera",
            camera_code="auth-disabled-camera",
            camera_name="Auth Disabled Camera",
            organization_id=settings.default_organization_id,
            scope=["*"],
        )
        request.state.camera_auth = context
        return context

    token = _token_from_credentials(credentials)
    if token is None:
        # fallback để vẫn hỗ trợ client gửi header thủ công trong một số proxy/test tool
        token = extract_bearer_token(request.headers.get("Authorization"))
    if not x_organization_id:
        raise AuthError(status.HTTP_400_BAD_REQUEST, ORG_HEADER_REQUIRED, "X-Organization-ID header is required")

    token_hash = hash_camera_token(token)
    token_row = db.execute(
        """
        SELECT ct.*, cc.client_code, cc.name AS camera_name, cc.is_active AS camera_is_active
        FROM camera_tokens ct
        JOIN camera_clients cc ON cc.camera_client_id = ct.camera_client_id
        WHERE ct.token_hash = ?
        """,
        (token_hash,),
    ).fetchone()

    if not token_row:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Invalid camera token")

    if int(token_row["is_revoked"] or 0) == 1:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Camera token revoked")

    if int(token_row["camera_is_active"] or 0) != 1:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Camera/service inactive")

    expires_at = token_row["expires_at"]
    if expires_at:
        # SQLite đang lưu ISO text. So sánh lexicographic ổn với ISO UTC/VN dạng YYYY-MM-DD HH:MM:SS.
        now_row = db.execute("SELECT datetime('now') AS now_utc").fetchone()
        if str(expires_at) < str(now_row["now_utc"]):
            raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Camera token expired")

    org_row = db.execute(
        """
        SELECT 1
        FROM camera_client_organizations cco
        JOIN organizations o ON o.organization_id = cco.organization_id
        WHERE cco.camera_client_id = ?
          AND cco.organization_id = ?
          AND o.is_active = 1
        """,
        (token_row["camera_client_id"], x_organization_id),
    ).fetchone()

    if not org_row:
        raise AuthError(
            status.HTTP_403_FORBIDDEN,
            ORG_MISMATCH,
            "Camera/service không có quyền gửi dữ liệu cho organization này",
        )

    context = CameraAuthContext(
        camera_client_id=str(token_row["camera_client_id"]),
        camera_code=str(token_row["client_code"]),
        camera_name=token_row["camera_name"],
        organization_id=x_organization_id,
        scope=_json_list(token_row["scope"]),
        token_id=token_row["token_id"],
    )
    request.state.camera_auth = context
    return context
