from __future__ import annotations

import base64
import hashlib
from typing import Any, Dict

import httpx
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
_jwk_clients: Dict[str, PyJWKClient] = {}

_discovery_cache: Dict[str, Dict[str, Any]] = {}

def get_provider_config(provider: str) -> Dict[str, str]:
    config = settings.oidc_providers.get(provider)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "UNKNOWN_OIDC_PROVIDER",
                "message": f"Provider '{provider}' chưa được cấu hình. Provider hợp lệ: {list(settings.oidc_providers.keys())}",
            },
        )
    return config


def get_jwk_client(provider: str) -> PyJWKClient:
    if provider not in _jwk_clients:
        config = get_provider_config(provider)
        if not config["jwks_url"]:
            raise RuntimeError(f"JWKS URL chưa được cấu hình cho provider '{provider}'")
        _jwk_clients[provider] = PyJWKClient(config["jwks_url"])
    return _jwk_clients[provider]


async def get_discovery(provider: str) -> Dict[str, Any]:
    if provider in _discovery_cache:
        return _discovery_cache[provider]

    config = get_provider_config(provider)
    url = f"{config['issuer']}/.well-known/openid-configuration"

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
        response.raise_for_status()

    _discovery_cache[provider] = response.json()
    return _discovery_cache[provider]


def calculate_at_hash(access_token: str) -> str:
    digest = hashlib.sha256(access_token.encode()).digest()
    left = digest[:16]
    return base64.urlsafe_b64encode(left).decode().rstrip("=")


async def verify_oidc_tokens(
    *,
    provider: str,
    id_token: str,
    access_token: str,
) -> dict[str, Any]:
    config = get_provider_config(provider)

    try:
        signing_key = get_jwk_client(provider).get_signing_key_from_jwt(id_token)

        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],  # hard-code, KHÔNG lấy alg từ header token chưa verify
            audience=config["client_id"],
            issuer=config["issuer"],
        )

        at_hash = claims.get("at_hash")
        if at_hash:
            expected = calculate_at_hash(access_token)
            if expected != at_hash:
                raise HTTPException(
                    status_code=401,
                    detail={"status": "AUTH_INVALID_TOKEN", "message": "at_hash verification failed"},
                )

        return claims

    except ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={"status": "TOKEN_EXPIRED", "message": "ID Token đã hết hạn"},
        )
    except InvalidAudienceError:
        raise HTTPException(
            status_code=401,
            detail={"status": "AUTH_INVALID_TOKEN", "message": "Audience không hợp lệ"},
        )
    except InvalidIssuerError:
        raise HTTPException(
            status_code=401,
            detail={"status": "AUTH_INVALID_TOKEN", "message": "Issuer không hợp lệ"},
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=401,
            detail={"status": "AUTH_INVALID_TOKEN", "message": str(exc)},
        )


def get_claim(claims: Dict[str, Any], dotted_path: str):
    value: Any = claims
    for part in dotted_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def extract_list_claim(claims: Dict[str, Any], dotted_path: str):
    value = get_claim(claims, dotted_path)
    if isinstance(value, list):
        return [str(v) for v in value]
    return []