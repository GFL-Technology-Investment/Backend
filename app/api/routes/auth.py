from __future__ import annotations
import logging

logger = logging.getLogger(__name__)
import json
import secrets
import sqlite3
import uuid
from typing import Optional, Literal

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Response, status
from pydantic import BaseModel

from app.api.deps.auth import require_internal_auth
from app.core import oidc
from app.core.auth_context import InternalAuthContext
from app.core.config import settings
from app.core.security import (
    create_internal_jwt,
    generate_refresh_token,
    hash_refresh_token,
    hash_password,
    verify_password
)
from app.core.status import AUTH_DEV_MODE_DISABLED

from app.database import get_db
from app.services.rbac_service import get_user_roles_and_permissions, assign_default_role   
from app.services.session_service import create_session, delete_session, get_session, update_session
router = APIRouter(prefix="/api/v1/auth")


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=settings.refresh_token_expire_seconds,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path=settings.refresh_cookie_path,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path=settings.refresh_cookie_path,
    )


class DevLoginRequest(BaseModel):
    username: str
    password: str


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
    }
}


async def _issue_refresh_token(
    db: sqlite3.Connection,
    user_id: str,
    organization_id: str,
    email: str,
    roles: list,
    permissions: list,
) -> tuple[str, str]:
    """Tạo refresh token mới, lưu hash vào DB, trả plaintext."""
    raw = generate_refresh_token()
    refresh_hash = hash_refresh_token(raw)
    session_id = await create_session(
        user_id=user_id,
        email=email,
        organization_id=organization_id,
        roles=roles,
        permissions=permissions,
        refresh_token_hash=refresh_hash,
    )
    now_sql = db.execute("SELECT datetime('now') AS n").fetchone()["n"]
    expires_sql = db.execute(
        "SELECT datetime('now', ? ) AS e",
        (f"+{settings.refresh_token_expire_seconds} seconds",),
    ).fetchone()["e"]

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
            str(uuid.uuid4()),
            user_id,
            refresh_hash,
            organization_id,
            session_id,
            now_sql,
            expires_sql,
        ),
    )
    return raw, session_id


def _build_token_response(
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
    resp = {
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
        resp.update(extra)
    return resp

def normalize_email(email: str) -> str:
    return email.strip().lower()
class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
async def login(
    payload: LoginRequest,
    response: Response,
    db: sqlite3.Connection = Depends(get_db),
):
    email = normalize_email(payload.email)

    if settings.auth_dev_mode:
        dev_user = DEV_USERS.get(email)

        if dev_user:
            if dev_user["password"] != payload.password:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "status": "INVALID_CREDENTIALS",
                        "message": "Invalid email or password",
                    },
                )

            try:
                with db:
                    refresh_raw, session_id = await _issue_refresh_token(
                        db=db,
                        user_id=dev_user["user_id"],
                        organization_id=dev_user["org_id"],
                        email=dev_user["email"],
                        roles=dev_user["roles"],
                        permissions=dev_user["permissions"],
                    )
            except Exception as e:
                logger.error("Lỗi cấp token Dev Mode: %s", e)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={"status": "SERVER_ERROR", "message": "Lỗi hệ thống khi đăng nhập"}
                )
                
            _set_refresh_cookie(response, refresh_raw)
            return _build_token_response(
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
        (email,),
    ).fetchone()

    if (
        not user_row
        or not user_row["password_hash"]
        or not verify_password(payload.password, user_row["password_hash"])
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "status": "INVALID_CREDENTIALS",
                "message": "Invalid email or password",
            },
        )

    roles, permissions = get_user_roles_and_permissions(
        db,
        user_row["user_id"],
    )

    try:
        with db:
            refresh_raw, session_id = await _issue_refresh_token(
                db=db,
                user_id=user_row["user_id"],
                organization_id=user_row["organization_id"],
                email=user_row["email"],
                roles=roles,
                permissions=permissions,
            )
    except Exception as e:
        logger.error("Lỗi cấp token Login: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "SERVER_ERROR", "message": "Lỗi hệ thống khi đăng nhập"}
        )
    _set_refresh_cookie(response, refresh_raw)

    return _build_token_response(
        user_id=user_row["user_id"],
        email=user_row["email"],
        org_id=user_row["organization_id"],
        roles=roles,
        permissions=permissions,
        refresh_token_raw=refresh_raw,
        session_id=session_id,
    )

@router.post("/refresh")
async def refresh_access_token(
    response: Response,
    refresh_token: str = Cookie(None, alias=settings.refresh_cookie_name),
    db: sqlite3.Connection = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "MISSING_TOKEN", "message": "Không tìm thấy Refresh Token"},
        )

    max_hops = 3  
    current_hash = hash_refresh_token(refresh_token)

    for _hop in range(max_hops):
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

                if session_id:
                    session = await get_session(session_id)
                    if not session:
                        db.execute("ROLLBACK")
                        db.execute("UPDATE refresh_tokens SET is_revoked = 1 WHERE token_hash = ?", (current_hash,))
                        db.commit()
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={"status": "SESSION_REVOKED", "message": "Phiên đăng nhập đã hết hạn hoặc bị thu hồi"},
                        )
                else:
                    session_id = await create_session(
                        user_id=user_row["user_id"],
                        email=user_row["email"],
                        organization_id=row["organization_id"],
                        roles=roles,
                        permissions=permissions,
                        refresh_token_hash=current_hash,
                    )

                new_raw = generate_refresh_token()
                new_hash = hash_refresh_token(new_raw)
                now_sql = db.execute("SELECT datetime('now') AS n").fetchone()["n"]
                expires_sql = db.execute(
                    "SELECT datetime('now', ?) AS e",
                    (f"+{settings.refresh_token_expire_seconds} seconds",),
                ).fetchone()["e"]

                await update_session(
                    session_id,
                    user_id=user_row["user_id"],
                    email=user_row["email"],
                    organization_id=row["organization_id"],
                    roles=roles,
                    permissions=permissions,
                    refresh_token_hash=new_hash,
                )

                db.execute(
                    """
                    INSERT INTO refresh_tokens
                        (refresh_token_id, user_id, token_hash, organization_id, session_id, issued_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (new_refresh_id, row["user_id"], new_hash, row["organization_id"], session_id, now_sql, expires_sql),
                )
                
                db.commit()
                _set_refresh_cookie(response, new_raw)

                return _build_token_response(
                    user_id=user_row["user_id"],
                    email=user_row["email"],
                    org_id=row["organization_id"],
                    roles=roles,
                    permissions=permissions,
                    refresh_token_raw=new_raw,
                    session_id=session_id,
                )
            
            else:
                db.execute("ROLLBACK")
                
                row = db.execute("SELECT * FROM refresh_tokens WHERE token_hash = ?", (current_hash,)).fetchone()

                if not row:
                    logger.warning("Refresh_token not found in DB, hash=%s...", current_hash[:12])
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
                            logger.info("Refresh race condition detected (within grace), chaining to successor")
                            current_hash = successor["token_hash"]
                            continue
                    
                    db.execute(
                        "UPDATE refresh_tokens SET is_revoked = 1 WHERE session_id = ?",
                        (row["session_id"],),
                    )
                    db.commit()
                    
                    if row["session_id"]:
                        await delete_session(row["session_id"])
                        
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail={
                            "status": "REFRESH_TOKEN_REUSE_DETECTED",
                            "message": "Phát hiện tái sử dụng refresh token. Phiên đăng nhập trên thiết bị này đã bị thu hồi.",
                        },
                    )

        except HTTPException:
            raise
        except Exception as e:
            db.execute("ROLLBACK")
            logger.error("Lỗi trong quá trình refresh token: %s", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"status": "SERVER_ERROR", "message": "Lỗi hệ thống khi làm mới token"},
            )

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"status": "REFRESH_CONFLICT", "message": "Quá nhiều request refresh đồng thời, vui lòng thử lại sau giây lát"},
    )

@router.post("/logout")
async def logout(
    response: Response,
    provider: Optional[str] = Form(None, description="Provider đã dùng để login (keycloak/azure). Bỏ trống nếu login bằng dev-login."),
    refresh_token: Optional[str] = Form(None, description="Refresh token cần thu hồi"),
    auth: InternalAuthContext = Depends(require_internal_auth),
    db: sqlite3.Connection = Depends(get_db),
):
    """Thu hồi refresh token. Access token vẫn có hiệu lực đến khi hết exp (chấp nhận được vì đã ngắn 15 phút)."""
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
        await delete_session(session_id)

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
            logger.warning("Không lấy được end_session_endpoint cho provider=%s: %s", provider, exc)
    _clear_refresh_cookie(response)
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


class AzureExchangeRequest(BaseModel):
    provider: Literal["keycloak", "azure"]
    id_token: str
    access_token: str
    org_id: Optional[str] = None


@router.post("/azure/exchange")
async def azure_exchange(
    response: Response,
    payload: AzureExchangeRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    claims = await oidc.verify_oidc_tokens(
        provider=payload.provider,
        id_token=payload.id_token,
        access_token=payload.access_token,
    )

    provider_sub = str(claims.get("sub") or "")
    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower() 

    if not provider_sub or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "AUTH_INVALID_TOKEN", "message": "Token thiếu sub hoặc email"},
        )

    provider_config = oidc.get_provider_config(payload.provider)
    roles = oidc.extract_list_claim(claims, provider_config["roles_claim"]) or ["guard"]
    permissions = oidc.extract_list_claim(claims, provider_config["permissions_claim"])
    org_id = (
        payload.org_id
        or oidc.get_claim(claims, provider_config["org_claim"])
        or settings.default_organization_id
    )

    try:
        with db: 
            identity_row = db.execute(
                "SELECT user_id FROM user_identity_providers WHERE provider = ? AND provider_sub = ?",
                (payload.provider, provider_sub),
            ).fetchone()

            if identity_row:
                user_row = db.execute(
                    "SELECT * FROM users WHERE user_id = ?", (identity_row["user_id"],)
                ).fetchone()
                if not user_row:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail={"status": "AUTH_INVALID_TOKEN", "message": "Liên kết định danh không hợp lệ"},
                    )
            else:
                is_email_verified = claims.get("email_verified", True) 

                user_row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

                if user_row:
                    if not is_email_verified:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail={"status": "EMAIL_NOT_VERIFIED", "message": "Email từ Provider chưa được xác minh, không thể liên kết tài khoản."}
                        )
                else:
                    new_user_id = str(uuid.uuid4())
                    db.execute(
                        """
                        INSERT INTO users (user_id, email, full_name, organization_id, roles, permissions, is_active)
                        VALUES (?, ?, ?, ?, ?, ?, 1)
                        """,
                        (new_user_id, email, claims.get("name", ""), org_id, json.dumps(roles), json.dumps(permissions)),
                    )
                    
                    assign_default_role(db, user_id=new_user_id, role_code="GUARD")
                    
                    user_row = db.execute("SELECT * FROM users WHERE user_id = ?", (new_user_id,)).fetchone()

                db.execute(
                    """
                    INSERT INTO user_identity_providers (identity_id, user_id, provider, provider_sub, email_at_link)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), user_row["user_id"], payload.provider, provider_sub, email),
                )

            if not int(user_row["is_active"] or 0):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"status": "USER_INACTIVE", "message": "Tài khoản đã bị khóa"},
                )

            user_id = user_row["user_id"]
            org_id = user_row["organization_id"]
            roles, permissions = get_user_roles_and_permissions(db, user_id)

            refresh_raw, session_id = await _issue_refresh_token(
                db=db,
                user_id=user_id,
                organization_id=org_id,
                email=user_row["email"],
                roles=roles,
                permissions=permissions,
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "SERVER_ERROR", "message": "Lỗi hệ thống trong quá trình đăng nhập."}
        )

    _set_refresh_cookie(response, refresh_raw)

    return _build_token_response(
        user_id=user_id,
        email=user_row["email"],
        org_id=org_id,
        roles=roles,
        permissions=permissions,
        refresh_token_raw=refresh_raw,
        session_id=session_id,
    )
