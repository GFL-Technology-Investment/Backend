from __future__ import annotations

import sqlite3
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field

from app.api.deps.auth import require_permission
from app.core.security import hash_password
from app.database import get_db
from app.services.rbac_service import get_user_roles_and_permissions

router = APIRouter()


def _safe_user_dict(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    roles, permissions = get_user_roles_and_permissions(db, row["user_id"])
    return {
        "user_id": row["user_id"],
        "email": row["email"],
        "full_name": row["full_name"],
        "organization_id": row["organization_id"],
        "is_active": bool(row["is_active"]),
        "roles": roles,
        "permissions": permissions,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("/api/v1/list/user")
async def get_list_user(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("system.user.update")),
):
    offset = (page - 1) * limit
    rows = db.execute(
        "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?", [limit, offset]
    ).fetchall()
    total = db.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]

    return {"total": total, "users": [_safe_user_dict(db, row) for row in rows]}


@router.get("/api/v1/user/{user_id}")
async def get_user_by_id(
    user_id: str,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("system.user.update")),
):
    row = db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return _safe_user_dict(db, row)


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: Optional[str] = None
    organization_id: str
    role_codes: List[str] = Field(default_factory=list, description="Danh sách role_code, vd [\"GUARD\"]")


@router.post("/api/v1/user", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: CreateUserRequest,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("system.user.create")),
):
    org_row = db.execute(
        "SELECT organization_id FROM organizations WHERE organization_id = ? AND is_active = 1",
        (payload.organization_id,),
    ).fetchone()
    if not org_row:
        raise HTTPException(status_code=400, detail={"status": "ORG_NOT_FOUND", "message": "Tổ chức không tồn tại hoặc đã bị khóa"})
    normalized_email = payload.email.strip().lower()
    existing = db.execute("SELECT user_id FROM users WHERE email = ?", (normalized_email,)).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail={"status": "EMAIL_ALREADY_EXISTS", "message": "Email đã được sử dụng"})

    role_rows = []
    for role_code in payload.role_codes:
        role_row = db.execute("SELECT role_id FROM roles WHERE role_code = ?", (role_code,)).fetchone()
        if not role_row:
            raise HTTPException(status_code=400, detail={"status": "ROLE_NOT_FOUND", "message": f"Role '{role_code}' không tồn tại"})
        role_rows.append(role_row)

    user_id = f"user-{uuid.uuid4().hex[:12]}"
    db.execute(
        """
        INSERT INTO users (user_id, email, full_name, organization_id, password_hash, roles, permissions, is_active)
        VALUES (?, ?, ?, ?, ?, '[]', '[]', 1)
        """,
        (user_id, normalized_email, payload.full_name, payload.organization_id, hash_password(payload.password)),
    )

    for role_row in role_rows:
        db.execute(
            "INSERT OR IGNORE INTO user_roles (user_role_id, user_id, role_id, assigned_by, assigned_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (str(uuid.uuid4()), user_id, role_row["role_id"], _auth.user_id),
        )

    db.commit()

    row = db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return _safe_user_dict(db, row)


class UpdateUserRequest(BaseModel):
    full_name: Optional[str] = None
    organization_id: Optional[str] = None
    is_active: Optional[bool] = None


@router.patch("/api/v1/user/{user_id}")
async def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("system.user.update")),
):
    row = db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    updates, params = [], []
    if payload.full_name is not None:
        updates.append("full_name = ?"); params.append(payload.full_name)
    if payload.organization_id is not None:
        org_row = db.execute("SELECT organization_id FROM organizations WHERE organization_id = ?", (payload.organization_id,)).fetchone()
        if not org_row:
            raise HTTPException(status_code=400, detail={"status": "ORG_NOT_FOUND", "message": "Tổ chức không tồn tại"})
        updates.append("organization_id = ?"); params.append(payload.organization_id)
    if payload.is_active is not None:
        updates.append("is_active = ?"); params.append(1 if payload.is_active else 0)

    if updates:
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(user_id)
        db.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?", params)
        db.commit()

    row = db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return _safe_user_dict(db, row)


@router.delete("/api/v1/user/{user_id}")
async def delete_user(
    user_id: str,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("system.user.delete")),
):
    """Soft-delete (is_active=0) — KHÔNG xóa cứng, vì user_id còn được tham
    chiếu ở refresh_tokens/audit_logs (lịch sử tra soát cần giữ nguyên)."""
    row = db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    db.execute("UPDATE users SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", (user_id,))
    db.execute("UPDATE refresh_tokens SET is_revoked = 1 WHERE user_id = ?", (user_id,))  # đăng xuất ngay lập tức
    db.commit()
    return {"status": "SUCCESS", "message": "Đã khóa tài khoản"}


class AssignRolesRequest(BaseModel):
    role_codes: List[str] = Field(description="Danh sách role_code MỚI — thay thế toàn bộ role hiện tại của user")


@router.put("/api/v1/user/{user_id}/roles")
async def assign_user_roles(
    user_id: str,
    payload: AssignRolesRequest,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("role.assign")),
):
    user_row = db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not user_row:
        raise HTTPException(status_code=404, detail="User not found")

    role_ids = []
    for role_code in payload.role_codes:
        role_row = db.execute("SELECT role_id FROM roles WHERE role_code = ?", (role_code,)).fetchone()
        if not role_row:
            raise HTTPException(status_code=400, detail={"status": "ROLE_NOT_FOUND", "message": f"Role '{role_code}' không tồn tại"})
        role_ids.append(role_row["role_id"])

    # Thay thế toàn bộ (xóa hết role cũ, gán lại đúng danh sách mới) — đơn
    # giản hơn cho FE (gửi nguyên danh sách mong muốn), thay vì phải tự tính
    # diff thêm/bớt.
    db.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
    for role_id in role_ids:
        db.execute(
            "INSERT INTO user_roles (user_role_id, user_id, role_id, assigned_by, assigned_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (str(uuid.uuid4()), user_id, role_id, _auth.user_id),
        )
    db.commit()

    roles, permissions = get_user_roles_and_permissions(db, user_id)
    return {"status": "SUCCESS", "user_id": user_id, "roles": roles, "permissions": permissions}


@router.get("/api/v1/roles")
async def list_roles(
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("role.assign")),
):
    rows = db.execute("SELECT role_id, role_code, role_name, description FROM roles ORDER BY role_name").fetchall()
    return {"roles": [dict(r) for r in rows]}


@router.get("/api/v1/permissions")
async def list_permissions(
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("role.assign")),
):
    rows = db.execute("SELECT permission_id, permission_code, permission_name, module_name FROM permissions ORDER BY module_name, permission_code").fetchall()
    return {"permissions": [dict(r) for r in rows]}

class UpdatePermissionRequest(BaseModel):
    permission_name: Optional[str] = None
    description: Optional[str] = None


@router.patch("/api/v1/permissions/{permission_id}")
async def update_permission(
    permission_id: str,
    payload: UpdatePermissionRequest,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("permission.assign")),
):
    """Chỉ sửa mô tả/tên hiển thị — KHÔNG cho đổi permission_code (đó là
    string được hard-code trong Depends(require_permission("...")) ở khắp
    routes, đổi giữa chừng sẽ làm mọi check quyền hiện có bị lệch ngay lập
    tức). Cũng không có API tạo/xóa permission — xem giải thích đầu câu trả lời."""
    row = db.execute("SELECT * FROM permissions WHERE permission_id = ?", (permission_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Permission not found")

    updates, params = [], []
    if payload.permission_name is not None:
        updates.append("permission_name = ?"); params.append(payload.permission_name)
    if payload.description is not None:
        updates.append("description = ?"); params.append(payload.description)

    if updates:
        params.append(permission_id)
        db.execute(f"UPDATE permissions SET {', '.join(updates)} WHERE permission_id = ?", params)
        db.commit()

    row = db.execute("SELECT * FROM permissions WHERE permission_id = ?", (permission_id,)).fetchone()
    return dict(row)