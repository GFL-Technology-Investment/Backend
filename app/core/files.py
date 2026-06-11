import os
import shutil
from typing import Optional
from urllib.parse import urlparse

from fastapi import Request, UploadFile

STATIC_FOLDER = "static"


def safe_extension(filename: Optional[str], default: str = ".jpg") -> str:
    if not filename:
        return default
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        return default
    return ext


def save_upload_file(upload_file: UploadFile, folder: str, prefix: str = "file") -> str:
    import uuid

    os.makedirs(folder, exist_ok=True)
    ext = safe_extension(upload_file.filename)
    file_name = f"{prefix}-{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(folder, file_name)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)

    return file_path


def to_static_url(file_path: Optional[str]) -> Optional[str]:
    if not file_path:
        return None
    normalized = file_path.replace("\\", "/")
    if normalized.startswith("static/"):
        return "/" + normalized
    return normalized


def absolute_url(request: Request, url_path: Optional[str]) -> Optional[str]:
    if not url_path:
        return None
    if url_path.startswith("http://") or url_path.startswith("https://"):
        return url_path
    return str(request.base_url).rstrip("/") + url_path


def static_url_to_local_path(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url)
    path = parsed.path if parsed.scheme else url
    if path.startswith("/static/"):
        return os.path.join(STATIC_FOLDER, path[len("/static/"):])
    if path.startswith("static/"):
        return path
    return None
