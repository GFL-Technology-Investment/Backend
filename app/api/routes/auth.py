from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps.auth import require_internal_auth
from app.core.auth_context import InternalAuthContext
from app.core.config import settings
from app.core.security import create_internal_jwt
from app.core.status import AUTH_DEV_MODE_DISABLED

router = APIRouter(prefix="/api/v1/auth")


class DevLoginRequest(BaseModel):
    """Payload test local, giả lập claims đã được công ty tổng cấu hình trong Azure AD."""

    user_id: str = Field("user-dev-001", description="Internal user id/sub")
    email: str = Field("guard@company.com", description="Email công ty")
    org_id: str = Field("org-001", description="Organization đang thao tác")
    roles: List[str] = Field(default_factory=lambda: ["guard"])
    permissions: List[str] = Field(default_factory=lambda: ["*"])


class AzureExchangeRequest(BaseModel):
    """Stub để giữ đúng luồng thiết kế: FE gửi Azure token về BE để đổi internal JWT.

    Giai đoạn PoC chưa verify Azure thật vì cần Tenant/Client/JWKS từ công ty tổng.
    """

    azure_access_token: str
    org_id: Optional[str] = None


@router.post("/dev-login")
async def dev_login(payload: DevLoginRequest):
    """Cấp JWT nội bộ để test các Internal APIs khi chưa nối Azure AD thật.

    Tắt bằng AUTH_DEV_MODE=false trong môi trường không phải local/test.
    """

    if not settings.auth_dev_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": AUTH_DEV_MODE_DISABLED, "message": "AUTH_DEV_MODE=false"},
        )

    token = create_internal_jwt(
        {
            "sub": payload.user_id,
            "email": payload.email,
            "org_id": payload.org_id,
            "roles": payload.roles,
            "permissions": payload.permissions,
        }
    )
    return {
        "status": "SUCCESS",
        "token_type": "Bearer",
        "access_token": token,
        "expires_in_seconds": settings.internal_jwt_expire_seconds,
        "usage": "Authorization: Bearer <access_token>",
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
