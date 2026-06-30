from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps.auth import require_internal_auth
from app.core.auth_context import InternalAuthContext
from app.core.config import settings
from app.core.security import create_internal_jwt
from app.core.status import AUTH_DEV_MODE_DISABLED

router = APIRouter(prefix="/api/v1/auth")


class DevLoginRequest(BaseModel):
    username: str
    password: str
DEV_USERS = {
    # 1. Tài khoản bảo vệ (Guard) của bạn
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
            "location_id": "loc-001",
            "gate_id": "gate-001"
        }
    },
    # 2. THÊM MỚI: Tài khoản TỔNG (Master) dùng để FE test full luồng
    "master@company.com": {
        "user_id": "user-master-999",
        "password": "masterpassword",
        "email": "master@company.com",
        "org_id": "org-001",
        "roles": ["admin", "guard"],
        "permissions": ["*"], # Cấp full quyền để FE test không bị chặn
        "camera": {
            "camera_id": "camera-master-999",
            "camera_token": settings.dev_camera_token,
            "location_id": "loc-001",
            "gate_id": "gate-001"
        }
    }
}

class AzureExchangeRequest(BaseModel):
    """Stub để giữ đúng luồng thiết kế: FE gửi Azure token về BE để đổi internal JWT.

    Giai đoạn PoC chưa verify Azure thật vì cần Tenant/Client/JWKS từ công ty tổng.
    """

    azure_access_token: str
    org_id: Optional[str] = None


@router.post("/dev-login")
async def dev_login(payload: DevLoginRequest):

    user = DEV_USERS.get(payload.username)
    """Cấp JWT nội bộ để test các Internal APIs khi chưa nối Azure AD thật."""

    if not settings.auth_dev_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "status": AUTH_DEV_MODE_DISABLED,
                "message": "AUTH_DEV_MODE=false",
            },
        )
    if not user or user["password"] != payload.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "status": "INVALID_CREDENTIALS",
                "message": "Invalid username or password",
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

    "camera": user["camera"],

    "usage": {
        "internal_api": "Authorization: Bearer <access_token>",
        "camera_api": "Authorization: Bearer <camera.camera_token>",
    },
}

@router.get("/me")
async def get_me(auth: InternalAuthContext = Depends(require_internal_auth)):
    return {"status": "SUCCESS", "data": auth.to_dict()}


@router.get("/dev-camera-token")
async def get_dev_camera_token():
    """Trả camera token test local. DB chỉ lưu hash token, không lưu plaintext."""

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


@router.post("/azure/exchange")
async def azure_exchange(_: AzureExchangeRequest):
    """Placeholder cho giai đoạn tích hợp Azure AD thật.

    Khi có AZURE_TENANT_ID/AZURE_CLIENT_ID/JWKS, endpoint này sẽ:
    1. Verify Azure token: signature, issuer, audience, expiry, tenant.
    2. Lấy claims user_id/email/org_id/roles/permissions.
    3. Map sang user nội bộ.
    4. Cấp JWT nội bộ cho Internal APIs.
    """

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "status": "AZURE_EXCHANGE_NOT_CONFIGURED",
            "message": "PoC hiện dùng /api/v1/auth/dev-login. Azure AD thật cần cấu hình tenant/client/JWKS trước.",
        },
    )
