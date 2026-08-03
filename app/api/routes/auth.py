from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Cookie, Depends, Form, Response, status
from fastapi import HTTPException
from pydantic import BaseModel

from app.api.deps.auth import require_internal_auth
from app.core.auth_context import InternalAuthContext
from app.core.config import settings
from app.database import get_db
from app.services import login_service, logout_service, oidc_service, refresh_service

router = APIRouter(prefix="/api/v1/auth")


class DevLoginRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
async def login(
    payload: LoginRequest,
    response: Response,
    db=Depends(get_db),
):
    return await login_service.login_user(
        email=payload.email,
        password=payload.password,
        response=response,
        db=db,
    )


@router.post("/refresh")
async def refresh_access_token(
    response: Response,
    refresh_token: str = Cookie(None, alias=settings.refresh_cookie_name),
    db=Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "MISSING_TOKEN", "message": "Không tìm thấy Refresh Token"},
        )
    return await refresh_service.refresh_access_token(
        response=response,
        refresh_token=refresh_token,
        db=db,
    )


@router.post("/logout")
async def logout(
    response: Response,
    provider: Optional[str] = Form(None, description="Provider đã dùng để login (keycloak/azure). Bỏ trống nếu login bằng dev-login."),
    refresh_token: Optional[str] = Form(None, description="Refresh token cần thu hồi"),
    auth: InternalAuthContext = Depends(require_internal_auth),
    db=Depends(get_db),
):
    return await logout_service.logout_user(
        response=response,
        provider=provider,
        refresh_token=refresh_token,
        auth=auth,
        db=db,
    )


@router.get("/me")
async def get_me(auth: InternalAuthContext = Depends(require_internal_auth)):
    return {"status": "SUCCESS", "data": auth.to_dict()}


@router.get("/dev-camera-token")
async def get_dev_camera_token():
    if not settings.auth_dev_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": "AUTH_DEV_MODE_DISABLED", "message": "AUTH_DEV_MODE=false"},
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
    db=Depends(get_db),
):
    return await oidc_service.exchange_oidc_tokens(
        provider=payload.provider,
        id_token=payload.id_token,
        access_token=payload.access_token,
        requested_org_id=payload.org_id,
        response=response,
        db=db,
    )
