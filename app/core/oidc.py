from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import Any

import jwt
from fastapi import HTTPException, status
from jwt import (
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidTokenError,
    PyJWKClient,
)

from app.core.config import settings


# ==========================================================
# JWKS CLIENT
# ==========================================================

@lru_cache(maxsize=1)
def get_jwk_client() -> PyJWKClient:
    if not settings.azure_jwks_url:
        raise RuntimeError("AZURE_JWKS_URL chưa được cấu hình")

    return PyJWKClient(settings.azure_jwks_url)


# ==========================================================
# at_hash
# ==========================================================

def calculate_at_hash(access_token: str) -> str:
    digest = hashlib.sha256(access_token.encode()).digest()

    left = digest[:16]

    return (
        base64.urlsafe_b64encode(left)
        .decode()
        .rstrip("=")
    )


# ==========================================================
# VERIFY OIDC
# ==========================================================

async def verify_oidc_tokens(
    *,
    id_token: str,
    access_token: str,
) -> dict[str, Any]:

    try:

        signing_key = get_jwk_client().get_signing_key_from_jwt(
            id_token
        )

        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.azure_client_id,
            issuer=settings.azure_issuer,
        )

        at_hash = claims.get("at_hash")

        if at_hash:

            expected = calculate_at_hash(access_token)

            if expected != at_hash:

                raise HTTPException(
                    status_code=401,
                    detail={
                        "status": "AUTH_INVALID_TOKEN",
                        "message": "at_hash verification failed",
                    },
                )

        return claims

    except ExpiredSignatureError:

        raise HTTPException(
            status_code=401,
            detail={
                "status": "TOKEN_EXPIRED",
                "message": "ID Token đã hết hạn",
            },
        )

    except InvalidAudienceError:

        raise HTTPException(
            status_code=401,
            detail={
                "status": "AUTH_INVALID_TOKEN",
                "message": "Audience không hợp lệ",
            },
        )

    except InvalidIssuerError:

        raise HTTPException(
            status_code=401,
            detail={
                "status": "AUTH_INVALID_TOKEN",
                "message": "Issuer không hợp lệ",
            },
        )

    except InvalidTokenError as exc:

        raise HTTPException(
            status_code=401,
            detail={
                "status": "AUTH_INVALID_TOKEN",
                "message": str(exc),
            },
        )


# ==========================================================
# CLAIM HELPERS
# ==========================================================

def get_claim(
    claims: dict[str, Any],
    dotted_path: str,
):

    value: Any = claims

    for part in dotted_path.split("."):

        if not isinstance(value, dict):
            return None

        value = value.get(part)

    return value


def extract_list_claim(
    claims: dict[str, Any],
    dotted_path: str,
):

    value = get_claim(claims, dotted_path)

    if isinstance(value, list):
        return [str(v) for v in value]

    return []