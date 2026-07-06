"""
Verify Keycloak / Azure AD Access Token cho endpoint
/api/v1/auth/azure/exchange.

Frontend (SPA) tự thực hiện Authorization Code + PKCE.

Backend chỉ có nhiệm vụ:

1. Download JWKS
2. Verify JWT signature
3. Verify iss
4. Verify aud
5. Trả về claims
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

import httpx
from fastapi import HTTPException, status
from jose import JWTError, jwt

from app.core.config import settings

# ============================================================
# JWKS CACHE
# ============================================================

_jwks_cache: Dict[str, Any] = {
    "keys": [],
    "fetched_at": 0.0,
}

JWKS_CACHE_TTL_SECONDS = 300


# ============================================================
# DOWNLOAD JWKS
# ============================================================

async def get_jwks() -> Dict[str, Any]:
    now = time.time()

    if (
        _jwks_cache["keys"]
        and now - _jwks_cache["fetched_at"] < JWKS_CACHE_TTL_SECONDS
    ):
        return _jwks_cache

    if not settings.azure_jwks_url:
        raise HTTPException(
            status_code=500,
            detail="AZURE_JWKS_URL chưa cấu hình",
        )

    print("\n========== DOWNLOAD JWKS ==========")
    print(settings.azure_jwks_url)

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(settings.azure_jwks_url)
        resp.raise_for_status()

    _jwks_cache["keys"] = resp.json()["keys"]
    _jwks_cache["fetched_at"] = now

    print(f"Loaded {_jwks_cache['keys'].__len__()} keys")

    return _jwks_cache


# ============================================================
# FIND KEY
# ============================================================

async def find_signing_key(kid: str):

    jwks = await get_jwks()

    for key in jwks["keys"]:
        if key["kid"] == kid:
            return key

    print("Key not found -> Refresh JWKS")

    _jwks_cache["keys"] = []

    jwks = await get_jwks()

    for key in jwks["keys"]:
        if key["kid"] == kid:
            return key

    raise HTTPException(
        status_code=401,
        detail=f"Không tìm thấy signing key kid={kid}",
    )


# ============================================================
# VERIFY TOKEN
# ============================================================

async def verify_azure_token(token: str) -> Dict[str, Any]:
    print("=" * 80)
    print("VERIFY FUNCTION IS RUNNING")
    print("=" * 80)

    header = jwt.get_unverified_header(token)
    print("HEADER:", header)

    claims = jwt.get_unverified_claims(token)
    print("UNVERIFIED CLAIMS:")
    print(claims)

    signing_key = await find_signing_key(header["kid"])

    print("VERIFYING SIGNATURE...")

    verified = jwt.decode(
        token,
        signing_key,
        algorithms=["RS256"],
        audience=settings.azure_client_id,
        issuer=settings.azure_issuer,
        options={
            "verify_at_hash": False,
        },
    )

    print("VERIFY SUCCESS")
    print(verified)

    return verified

# ============================================================
# CLAIM UTILS
# ============================================================

def get_claim(claims: Dict[str, Any], dotted_path: str) -> Any:

    value: Any = claims

    for part in dotted_path.split("."):

        if not isinstance(value, dict):
            return None

        value = value.get(part)

    return value


def extract_list_claim(
    claims: Dict[str, Any],
    dotted_path: str,
) -> List[str]:

    value = get_claim(claims, dotted_path)

    if isinstance(value, list):
        return [str(x) for x in value]

    return []