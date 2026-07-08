from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps.auth import require_internal_auth
from app.core import oidc
from app.core.auth_context import InternalAuthContext
from app.core.config import settings
from app.core.security import create_internal_jwt
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
            "ticket.print", "access.checkout", "history.read"
        ],
        "camera": {
            "camera_id": "camera-dev-001",
            "camera_token": settings.dev_camera_token,
            "org_id": "org-001",
            "location_id": "loc-001",
            "gate_id": "gate-001"
        }
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


@router.post("/dev-login")
async def dev_login(payload: DevLoginRequest):
    if not settings.auth_dev_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "status": AUTH_DEV_MODE_DISABLED,
                "message": "AUTH_DEV_MODE=false",
            },
        )

    user = DEV_USERS.get(payload.username)

    if user is None or user["password"] != payload.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "status": "INVALID_CREDENTIALS",
                "message": "Sai tài khoản hoặc mật khẩu",
            },
        )

    token = create_internal_jwt(
        {
            "sub": user["user_id"],
            "email": user["email"],
            "org_id": user["org_id"],
            "roles": user["roles"],
            "permissions": user["permissions"],
        }
    )

    return {
        "status": "SUCCESS",
        "token_type": "Bearer",
        "access_token": token,
        "expires_in_seconds": settings.internal_jwt_expire_seconds,
        "user": {
            "user_id": user["user_id"],
            "username": payload.username,
            "email": user["email"],
            "organization_id": user["org_id"],
            "roles": user["roles"],
            "permissions": user["permissions"],
        },
        "camera": user.get("camera"),
    }


@router.get("/me")
async def get_me(
    auth: InternalAuthContext = Depends(require_internal_auth),
):
    return {
        "status": "SUCCESS",
        "data": auth.to_dict(),
    }


@router.get("/dev-camera-token")
async def get_dev_camera_token():

    if not settings.auth_dev_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "status": AUTH_DEV_MODE_DISABLED,
                "message": "AUTH_DEV_MODE=false",
            },
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

    email = (
        claims.get("email")
        or claims.get("preferred_username")
    )

    if not azure_user_id or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "status": "AUTH_INVALID_TOKEN",
                "message": "Token thiếu sub hoặc email",
            },
        )

    roles = (
        oidc.extract_list_claim(
            claims,
            settings.azure_roles_claim,
        )
        or ["guard"]
    )

    permissions = oidc.extract_list_claim(
        claims,
        settings.azure_permissions_claim,
    )

    org_id = (
        payload.org_id
        or oidc.get_claim(
            claims,
            settings.azure_org_claim,
        )
        or settings.default_organization_id
    )

    user_row = db.execute(
        """
        SELECT *
        FROM users
        WHERE email=?
        """,
        (email,),
    ).fetchone()

    if user_row is None:

        user_id = f"azure-{secrets.token_hex(8)}"

        db.execute(
            """
            INSERT INTO users
            (
                user_id,
                email,
                full_name,
                organization_id,
                roles,
                permissions,
                azure_user_id,
                is_active
            )
            VALUES
            (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                user_id,
                email,
                claims.get("name", ""),
                org_id,
                json.dumps(roles),
                json.dumps(permissions),
                azure_user_id,
            ),
        )

        db.commit()

    else:

        if not int(user_row["is_active"] or 0):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "status": "USER_INACTIVE",
                    "message": "Tài khoản đã bị khóa",
                },
            )

        user_id = user_row["user_id"]

        org_id = user_row["organization_id"]

        roles = json.loads(user_row["roles"] or "[]")

        permissions = json.loads(
            user_row["permissions"] or "[]"
        )

        if not user_row["azure_user_id"]:

            db.execute(
                """
                UPDATE users
                SET azure_user_id=?
                WHERE user_id=?
                """,
                (
                    azure_user_id,
                    user_id,
                ),
            )

            db.commit()

    internal_token = create_internal_jwt(
        {
            "sub": user_id,
            "email": email,
            "org_id": org_id,
            "roles": roles,
            "permissions": permissions,
        }
    )

    return {
        "status": "SUCCESS",
        "token_type": "Bearer",
        "access_token": internal_token,
        "expires_in_seconds": settings.internal_jwt_expire_seconds,
        "user": {
            "user_id": user_id,
            "username": email,
            "email": email,
            "organization_id": org_id,
            "roles": roles,
            "permissions": permissions,
        },
    }
@router.post("/logout")
async def logout(
    auth: InternalAuthContext = Depends(require_internal_auth),
):
    logout_url = (
        f"{settings.azure_issuer}"
        "/protocol/openid-connect/logout"
        f"?post_logout_redirect_uri={settings.frontend_url}/login"
    )

    return {
        "status": "SUCCESS",
        "logout_url": logout_url,
    }