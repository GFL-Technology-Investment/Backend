"""Cấu hình dùng chung cho API access-control PoC.

File này chỉ chứa constant/path để tránh hard-code rải rác trong router.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_title: str = "OCR CCCD + Access Control API"
    app_version: str = "2.0.0"

    upload_folder: str = "uploads"
    static_folder: str = "static"
    media_folder: str = "static/media"
    cccd_original_folder: str = "static/cccd_originals"
    cccd_face_folder: str = "static/cccd_faces"
    tickets_folder: str = "static/tickets"

    timezone_name: str = "Asia/Ho_Chi_Minh"
    time_format: str = "%Y-%m-%d %H:%M:%S"

    default_organization_id: str = "org-001"
    default_location_id: str = "loc-001"
    default_gate_id: str = "gate-001"
    default_gate_name: str = "Cổng vào 01"


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
