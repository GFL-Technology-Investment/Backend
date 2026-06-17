"""Security helpers dùng cho Auth nội bộ và Auth Camera.

Không thêm dependency ngoài: JWT nội bộ được ký HS256 bằng stdlib hmac/hashlib.
Phần Azure AD thật sẽ verify JWT bằng JWKS ở giai đoạn tích hợp Azure thật.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.status import AUTH_INVALID_TOKEN, AUTH_TOKEN_EXPIRED, AUTH_MISSING_TOKEN


class AuthError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail={"status": code, "message": message})


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _json_dumps(data: Dict[str, Any]) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def create_internal_jwt(payload: Dict[str, Any], expires_in_seconds: Optional[int] = None) -> str:
    """Tạo JWT nội bộ HS256 để FE gọi Internal APIs trong môi trường test/PoC."""

    now = int(time.time())
    exp = now + (expires_in_seconds or settings.internal_jwt_expire_seconds)
    token_payload = {
        **payload,
        "type": "internal",
        "iat": now,
        "exp": exp,
        "iss": settings.internal_jwt_issuer,
        "aud": settings.internal_jwt_audience,
    }
    header = {"alg": "HS256", "typ": "JWT"}

    signing_input = f"{_b64url_encode(_json_dumps(header))}.{_b64url_encode(_json_dumps(token_payload))}"
    signature = hmac.new(
        settings.internal_jwt_secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_internal_jwt(token: str) -> Dict[str, Any]:
    """Verify chữ ký + exp + issuer/audience của JWT nội bộ."""

    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Invalid JWT format") from exc

    signing_input = f"{header_b64}.{payload_b64}"
    expected_sig = hmac.new(
        settings.internal_jwt_secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()

    try:
        provided_sig = _b64url_decode(signature_b64)
    except Exception as exc:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Invalid JWT signature encoding") from exc

    if not hmac.compare_digest(expected_sig, provided_sig):
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Invalid JWT signature")

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as exc:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Invalid JWT payload") from exc

    if header.get("alg") != "HS256":
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Unsupported JWT alg")

    now = int(time.time())
    if int(payload.get("exp", 0)) < now:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_TOKEN_EXPIRED, "Internal token expired")

    if payload.get("type") != "internal":
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Token is not an internal token")

    if payload.get("iss") != settings.internal_jwt_issuer:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Invalid token issuer")

    if payload.get("aud") != settings.internal_jwt_audience:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Invalid token audience")

    return payload


def extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_MISSING_TOKEN, "Missing Authorization header")

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthError(status.HTTP_401_UNAUTHORIZED, AUTH_INVALID_TOKEN, "Authorization header must be Bearer token")

    return parts[1]


def hash_camera_token(token: str) -> str:
    """Hash camera token trước khi lưu DB. Không lưu plaintext token."""

    pepper = settings.camera_token_hash_pepper or settings.internal_jwt_secret
    return hmac.new(pepper.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_camera_token_hash(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_camera_token(token), token_hash)


def generate_token(prefix: str = "tok") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"
