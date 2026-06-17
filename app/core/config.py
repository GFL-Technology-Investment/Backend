"""Cấu hình dùng chung cho API access-control PoC.

File này chứa path/runtime settings và auth settings để tránh hard-code rải rác.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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


@dataclass(frozen=True)
class Settings:
    app_title: str = "OCR CCCD + Access Control API"
    app_version: str = "2.1.0-auth"

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
    internal_jwt_expire_seconds: int = _env_int("INTERNAL_JWT_EXPIRE_SECONDS", 60 * 60 * 8)

    # Camera Auth
    camera_token_hash_pepper: str = os.getenv("CAMERA_TOKEN_HASH_PEPPER", "dev-camera-token-pepper-change-me")
    dev_camera_token: str = os.getenv("DEV_CAMERA_TOKEN", "dev-camera-token")
    dev_camera_client_id: str = os.getenv("DEV_CAMERA_CLIENT_ID", "cam-client-001")
    dev_camera_code: str = os.getenv("DEV_CAMERA_CODE", "cam-gate-01")
    dev_camera_name: str = os.getenv("DEV_CAMERA_NAME", "Camera cổng vào 01")

    # Azure AD fields để tích hợp thật ở giai đoạn sau.
    # Hiện tại PoC dùng dev-login để test internal JWT trước.
    azure_tenant_id: str = os.getenv("AZURE_TENANT_ID", "")
    azure_client_id: str = os.getenv("AZURE_CLIENT_ID", "")
    azure_issuer: str = os.getenv("AZURE_ISSUER", "")
    azure_jwks_url: str = os.getenv("AZURE_JWKS_URL", "")
    azure_org_claim: str = os.getenv("AZURE_ORG_CLAIM", "org_id")
    azure_roles_claim: str = os.getenv("AZURE_ROLES_CLAIM", "roles")
    azure_permissions_claim: str = os.getenv("AZURE_PERMISSIONS_CLAIM", "permissions")


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
