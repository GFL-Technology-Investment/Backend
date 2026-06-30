
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Optional

from fastapi import Depends, Header, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth_context import CameraAuthContext, InternalAuthContext
from app.core.cache import cache_get, cache_set
from app.core.config import settings
from app.core.security import AuthError, decode_internal_jwt, extract_bearer_token, hash_camera_token
from app.core.status import (
    AUTH_INVALID_TOKEN,
    ORG_HEADER_REQUIRED,
    ORG_MISMATCH,
    PERMISSION_DENIED,
)
from app.database import get_db

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

USER_CACHE_TTL_SECONDS = 90      # ngắn vì cần phát hiện sớm nếu user/org bị khóa
CAMERA_CACHE_TTL_SECONDS = 300   # camera ít đổi quyền hơn user, TTL dài hơn chấp nhận được


def _token_from_credentials(credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    """Return raw token from Swagger/Postman Authorization: Bearer header."""
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


def _resolve_token(credentials: Optional[HTTPAuthorizationCredentials], request: Request) -> Optional[str]:
    """Ưu tiên HTTPBearer (Swagger), fallback đọc header thủ công cho proxy/test tool."""
    token = _token_from_credentials(credentials)
    if token is None:
        token = extract_bearer_token(request.headers.get("Authorization"))
    return token


# --------------------------------------------------------------------------
# Internal auth (FE/user)
# --------------------------------------------------------------------------

async def require_internal_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    db: sqlite3.Connection = Depends(get_db),
) -> InternalAuthContext:
    """Verify JWT nội bộ và inject InternalAuthContext vào request.state."""

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

    raw_token = _resolve_token(credentials, request)

    # Verify chữ ký + exp luôn chạy tại chỗ mỗi request, không cache bước này.
    payload = decode_internal_jwt(raw_token)

    user_id = str(payload.get("sub") or payload.get("user_id") or "")
    email = str(payload.get("email") or "")
    organization_id = str(payload.get("org_id") or payload.get("organization_id") or "")
    roles = [str(item) for item in payload.get("roles", [])]
    permissions = [str(item) for item in payload.get("permissions", [])]

    if not user_id or not email or not organization_id:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Internal token missing required claims")

    cache_key = f"auth:user:{user_id}:{organization_id}"
    cached = await cache_get(cache_key)

    if cached is None:
        row = db.execute("SELECT * FROM users WHERE user_id = ? AND is_active = 1", (user_id,)).fetchone()
        if not row:
            raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Internal user not found or inactive")

        org_row = db.execute(
            "SELECT * FROM organizations WHERE organization_id = ? AND is_active = 1", (organization_id,)
        ).fetchone()
        if not org_row:
            raise AuthError(status.HTTP_403_FORBIDDEN, ORG_MISMATCH, "Organization not found or inactive")

        await cache_set(cache_key, {"valid": True}, ttl_seconds=USER_CACHE_TTL_SECONDS)

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


# --------------------------------------------------------------------------
# Camera/service auth
# --------------------------------------------------------------------------

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

    token = _resolve_token(credentials, request)
    if not x_organization_id:
        raise AuthError(status.HTTP_400_BAD_REQUEST, ORG_HEADER_REQUIRED, "X-Organization-ID header is required")

    token_hash = hash_camera_token(token)
    cache_key = f"auth:camera:{token_hash}:{x_organization_id}"
    cached = await cache_get(cache_key)

    if cached is not None:
        context = CameraAuthContext(**cached)
        request.state.camera_auth = context
        return context

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

    context_data = {
        "camera_client_id": str(token_row["camera_client_id"]),
        "camera_code": str(token_row["client_code"]),
        "camera_name": token_row["camera_name"],
        "organization_id": x_organization_id,
        "scope": _json_list(token_row["scope"]),
        "token_id": token_row["token_id"],
    }

    # Chỉ cache khi expires_at còn xa hơn TTL cache, tránh cache "sống" lâu hơn token thật.
    ttl = CAMERA_CACHE_TTL_SECONDS
    await cache_set(cache_key, context_data, ttl_seconds=ttl)

    context = CameraAuthContext(**context_data)
    request.state.camera_auth = context
    return context