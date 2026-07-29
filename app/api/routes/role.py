from __future__ import annotations

import sqlite3
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps.auth import require_permission
from app.database import get_db

router = APIRouter()


@router.get("/api/v1/roles")
async def list_roles(db: sqlite3.Connection = Depends(get_db), _auth=Depends(require_permission("role.assign"))):
    rows = db.execute("SELECT * FROM roles ORDER BY role_name").fetchall()
    return {"roles": [dict(r) for r in rows]}


@router.get("/api/v1/roles/{role_id}")
async def get_role(role_id: str, db: sqlite3.Connection = Depends(get_db), _auth=Depends(require_permission("role.assign"))):
    row = db.execute("SELECT * FROM roles WHERE role_id = ?", (role_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Role not found")

    permission_rows = db.execute(
        """
        SELECT p.permission_code FROM role_permissions rp
        JOIN permissions p ON p.permission_id = rp.permission_id
        WHERE rp.role_id = ?
        """,
        (role_id,),
    ).fetchall()

    result = dict(row)
    result["permission_codes"] = [r["permission_code"] for r in permission_rows]
    return result


class CreateRoleRequest(BaseModel):
    role_code: str
    role_name: str
    description: Optional[str] = None


@router.post("/api/v1/roles", status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: CreateRoleRequest,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("role.assign")),
):
    existing = db.execute("SELECT role_id FROM roles WHERE role_code = ?", (payload.role_code,)).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail={"status": "ROLE_ALREADY_EXISTS", "message": "role_code đã tồn tại"})

    role_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO roles (role_id, role_code, role_name, description, is_system) VALUES (?, ?, ?, ?, 0)",
        (role_id, payload.role_code.upper(), payload.role_name, payload.description),
    )
    db.commit()
    return {"role_id": role_id, "role_code": payload.role_code.upper(), "role_name": payload.role_name}


class UpdateRoleRequest(BaseModel):
    role_name: Optional[str] = None
    description: Optional[str] = None


@router.patch("/api/v1/roles/{role_id}")
async def update_role(
    role_id: str,
    payload: UpdateRoleRequest,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("role.assign")),
):
    """Cố tình KHÔNG cho sửa role_code — đây là giá trị được nhúng thẳng vào
    JWT (claim "roles"), đổi giữa chừng sẽ làm mọi token cũ đang lưu ở FE
    tham chiếu sai role, và mọi nơi check role cứng theo string sẽ lệch."""
    row = db.execute("SELECT * FROM roles WHERE role_id = ?", (role_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Role not found")

    updates, params = [], []
    if payload.role_name is not None:
        updates.append("role_name = ?"); params.append(payload.role_name)
    if payload.description is not None:
        updates.append("description = ?"); params.append(payload.description)

    if updates:
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(role_id)
        db.execute(f"UPDATE roles SET {', '.join(updates)} WHERE role_id = ?", params)
        db.commit()

    row = db.execute("SELECT * FROM roles WHERE role_id = ?", (role_id,)).fetchone()
    return dict(row)


@router.delete("/api/v1/roles/{role_id}")
async def delete_role(role_id: str, db: sqlite3.Connection = Depends(get_db), _auth=Depends(require_permission("role.assign"))):
    row = db.execute("SELECT * FROM roles WHERE role_id = ?", (role_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Role not found")

    if int(row["is_system"] or 0):
        raise HTTPException(status_code=403, detail={"status": "SYSTEM_ROLE_PROTECTED", "message": "Không thể xóa role hệ thống"})

    in_use = db.execute("SELECT COUNT(*) AS total FROM user_roles WHERE role_id = ?", (role_id,)).fetchone()["total"]
    if in_use > 0:
        raise HTTPException(
            status_code=409,
            detail={"status": "ROLE_IN_USE", "message": f"Còn {in_use} user đang được gán role này — gỡ hết trước khi xóa"},
        )

    db.execute("DELETE FROM roles WHERE role_id = ?", (role_id,))
    db.commit()
    return {"status": "SUCCESS", "message": "Đã xóa role"}


class SetRolePermissionsRequest(BaseModel):
    permission_codes: List[str]


@router.put("/api/v1/roles/{role_id}/permissions")
async def set_role_permissions(
    role_id: str,
    payload: SetRolePermissionsRequest,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("permission.assign")),
):
    """Thay thế TOÀN BỘ permission của role bằng danh sách mới gửi lên —
    đây là hành động mạnh nhất trong toàn bộ hệ thống RBAC (ảnh hưởng NGAY
    LẬP TỨC mọi user đang có role này), nên gắn permission riêng biệt
    ("permission.assign"), tách khỏi "role.assign" (chỉ gán role cho 1 user)."""
    role_row = db.execute("SELECT role_id, is_system FROM roles WHERE role_id = ?", (role_id,)).fetchone()
    if not role_row:
        raise HTTPException(status_code=404, detail="Role not found")

    permission_ids = []
    for code in payload.permission_codes:
        p_row = db.execute("SELECT permission_id FROM permissions WHERE permission_code = ?", (code,)).fetchone()
        if not p_row:
            raise HTTPException(status_code=400, detail={"status": "PERMISSION_NOT_FOUND", "message": f"Permission '{code}' không tồn tại"})
        permission_ids.append(p_row["permission_id"])

    db.execute("DELETE FROM role_permissions WHERE role_id = ?", (role_id,))
    for permission_id in permission_ids:
        db.execute(
            "INSERT INTO role_permissions (role_permission_id, role_id, permission_id) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), role_id, permission_id),
        )
    db.commit()
    return {"status": "SUCCESS", "role_id": role_id, "permission_codes": payload.permission_codes}