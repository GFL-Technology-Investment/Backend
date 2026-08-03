from __future__ import annotations

import json
import sqlite3
import uuid

from fastapi import HTTPException, Response, status

from app.core import oidc
from app.core.config import settings
from app.core.status import AUTH_SESSION_STORE_UNAVAILABLE
from app.services.rbac_service import assign_default_role, get_user_roles_and_permissions
from app.services.session_service import SessionStoreUnavailable
from app.services.token_service import build_token_response, issue_refresh_token, set_refresh_cookie


def _session_store_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"status": AUTH_SESSION_STORE_UNAVAILABLE, "message": "Session store unavailable"},
    )


async def exchange_oidc_tokens(
    *,
    provider: str,
    id_token: str,
    access_token: str,
    requested_org_id: str | None,
    response: Response,
    db: sqlite3.Connection,
) -> dict:
    claims = await oidc.verify_oidc_tokens(
        provider=provider,
        id_token=id_token,
        access_token=access_token,
    )

    provider_sub = str(claims.get("sub") or "")
    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if not provider_sub or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "AUTH_INVALID_TOKEN", "message": "Token thiếu sub hoặc email"},
        )

    provider_config = oidc.get_provider_config(provider)
    roles = oidc.extract_list_claim(claims, provider_config["roles_claim"]) or ["guard"]
    permissions = oidc.extract_list_claim(claims, provider_config["permissions_claim"])
    org_id = (
        requested_org_id
        or oidc.get_claim(claims, provider_config["org_claim"])
        or settings.default_organization_id
    )

    try:
        with db:
            identity_row = db.execute(
                "SELECT user_id FROM user_identity_providers WHERE provider = ? AND provider_sub = ?",
                (provider, provider_sub),
            ).fetchone()

            if identity_row:
                user_row = db.execute(
                    "SELECT * FROM users WHERE user_id = ?", (identity_row["user_id"],)
                ).fetchone()
                if not user_row:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail={"status": "AUTH_INVALID_TOKEN", "message": "Liên kết định danh không hợp lệ"},
                    )
            else:
                is_email_verified = claims.get("email_verified", True)
                user_row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

                if user_row:
                    if not is_email_verified:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail={"status": "EMAIL_NOT_VERIFIED", "message": "Email từ Provider chưa được xác minh, không thể liên kết tài khoản."},
                        )
                else:
                    new_user_id = str(uuid.uuid4())
                    db.execute(
                        """
                        INSERT INTO users (user_id, email, full_name, organization_id, roles, permissions, is_active)
                        VALUES (?, ?, ?, ?, ?, ?, 1)
                        """,
                        (new_user_id, email, claims.get("name", ""), org_id, json.dumps(roles), json.dumps(permissions)),
                    )
                    assign_default_role(db, user_id=new_user_id, role_code="GUARD")
                    user_row = db.execute("SELECT * FROM users WHERE user_id = ?", (new_user_id,)).fetchone()

                db.execute(
                    """
                    INSERT INTO user_identity_providers (identity_id, user_id, provider, provider_sub, email_at_link)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), user_row["user_id"], provider, provider_sub, email),
                )

            if not int(user_row["is_active"] or 0):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"status": "USER_INACTIVE", "message": "Tài khoản đã bị khóa"},
                )

            user_id = user_row["user_id"]
            org_id = user_row["organization_id"]
            roles, permissions = get_user_roles_and_permissions(db, user_id)
            refresh_raw, session_id = await issue_refresh_token(
                db=db,
                user_id=user_id,
                organization_id=org_id,
                email=user_row["email"],
                roles=roles,
                permissions=permissions,
            )
    except HTTPException:
        raise
    except SessionStoreUnavailable as exc:
        raise _session_store_error() from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "SERVER_ERROR", "message": "Lỗi hệ thống trong quá trình đăng nhập."},
        ) from exc

    set_refresh_cookie(response, refresh_raw)
    return build_token_response(
        user_id=user_id,
        email=user_row["email"],
        org_id=org_id,
        roles=roles,
        permissions=permissions,
        refresh_token_raw=refresh_raw,
        session_id=session_id,
    )
