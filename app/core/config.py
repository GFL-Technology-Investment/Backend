from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict
from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _build_oidc_providers() -> Dict[str, Dict[str, str]]:
    providers: Dict[str, Dict[str, str]] = {}

    keycloak_issuer = os.getenv("KEYCLOAK_ISSUER", "")
    if keycloak_issuer:
        providers["keycloak"] = {
            "issuer": keycloak_issuer,
            "jwks_url": os.getenv("KEYCLOAK_JWKS_URL") or f"{keycloak_issuer}/protocol/openid-connect/certs",
            "client_id": os.getenv("KEYCLOAK_CLIENT_ID", ""),
            "org_claim": os.getenv("KEYCLOAK_ORG_CLAIM", "org_id"),
            "roles_claim": os.getenv("KEYCLOAK_ROLES_CLAIM", "realm_access.roles"),
            "permissions_claim": os.getenv("KEYCLOAK_PERMISSIONS_CLAIM", "permissions"),
        }

    azure_tenant_id = os.getenv("AZURE_TENANT_ID", "")
    if azure_tenant_id:
        default_issuer = f"https://login.microsoftonline.com/{azure_tenant_id}/v2.0"
        default_jwks_url = f"https://login.microsoftonline.com/{azure_tenant_id}/discovery/v2.0/keys"

        providers["azure"] = {
            "issuer": os.getenv("AZURE_ISSUER") or default_issuer,
            "jwks_url": os.getenv("AZURE_JWKS_URL") or default_jwks_url,
            "client_id": os.getenv("AZURE_CLIENT_ID", ""),
            "org_claim": os.getenv("AZURE_ORG_CLAIM", "org_id"),
            "roles_claim": os.getenv("AZURE_ROLES_CLAIM", "roles"),
            "permissions_claim": os.getenv("AZURE_PERMISSIONS_CLAIM", "permissions"),
        }

    return providers


@dataclass(frozen=True)
class Settings:
    app_title: str = "OCR CCCD + Access Control API"
    app_version: str = "2.2.0-multi-idp"

    upload_folder: str = "uploads"
    static_folder: str = "static"
    media_folder: str = "static/media"
    cccd_original_folder: str = "static/cccd_originals"
    cccd_face_folder: str = "static/cccd_faces"
    tickets_folder: str = "static/tickets"

    timezone_name: str = "Asia/Ho_Chi_Minh"
    time_format: str = "%Y-%m-%d %H:%M:%S"

    default_organization_id: str = os.getenv("DEFAULT_ORGANIZATION_ID", "org-001")
    default_location_id: str = os.getenv("DEFAULT_LOCATION_ID", "loc-001")
    default_gate_id: str = os.getenv("DEFAULT_GATE_ID", "gate-001")
    default_gate_name: str = os.getenv("DEFAULT_GATE_NAME", "Cổng vào 01")

    # Auth layer
    auth_enabled: bool = _env_bool("AUTH_ENABLED", True)
    auth_dev_mode: bool = _env_bool("AUTH_DEV_MODE", True)

    # Internal Auth / JWT nội bộ
    internal_jwt_secret: str = os.getenv("INTERNAL_JWT_SECRET", "dev-internal-jwt-secret-change-me")
    internal_jwt_issuer: str = os.getenv("INTERNAL_JWT_ISSUER", "gfl-core")
    internal_jwt_audience: str = os.getenv("INTERNAL_JWT_AUDIENCE", "gfl-internal-api")
    internal_jwt_expire_seconds: int = _env_int("INTERNAL_JWT_EXPIRE_SECONDS", 60 * 15)

    # Refresh token
    refresh_token_expire_seconds: int = _env_int("REFRESH_TOKEN_EXPIRE_SECONDS", 60 * 60 * 24 * 7)
    refresh_token_hash_pepper: str = os.getenv("REFRESH_TOKEN_HASH_PEPPER", "dev-refresh-token-pepper-change-me")
    refresh_token_rotation_grace_seconds: int = _env_int("REFRESH_TOKEN_ROTATION_GRACE_SECONDS", 10)
    refresh_cookie_name: str = os.getenv("REFRESH_COOKIE_NAME", "gfl_refresh_token")
    refresh_cookie_secure: bool = _env_bool("REFRESH_COOKIE_SECURE", False)
    refresh_cookie_samesite: str = os.getenv("REFRESH_COOKIE_SAMESITE", "lax")
    refresh_cookie_path: str = os.getenv("REFRESH_COOKIE_PATH", "/api/v1/auth")

    # Redis
    redis_enabled: bool = _env_bool("REDIS_ENABLED", False)
    redis_url: str = os.getenv(
        "REDIS_URL",
        "redis://localhost:6379/0",
    )

    # Camera Auth
    camera_token_hash_pepper: str = os.getenv("CAMERA_TOKEN_HASH_PEPPER", "dev-camera-token-pepper-change-me")
    dev_camera_token: str = os.getenv("DEV_CAMERA_TOKEN", "dev-camera-token")
    dev_camera_client_id: str = os.getenv("DEV_CAMERA_CLIENT_ID", "cam-client-001")
    dev_camera_code: str = os.getenv("DEV_CAMERA_CODE", "cam-gate-01")
    dev_camera_name: str = os.getenv("DEV_CAMERA_NAME", "Camera cổng vào 01")

    # SSO — nhiều Identity Provider cùng lúc (Keycloak test + Azure AD thật).
    # Xem _build_oidc_providers() phía trên để biết cách thêm provider mới.
    oidc_providers: Dict[str, Dict[str, str]] = field(default_factory=_build_oidc_providers)

    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:5174")


settings = Settings()


def ensure_runtime_folders() -> None:
    for folder in [
        settings.upload_folder,
        settings.static_folder,
        settings.media_folder,
        settings.cccd_original_folder,
        settings.cccd_face_folder,
        settings.tickets_folder,
    ]:
        os.makedirs(folder, exist_ok=True)
