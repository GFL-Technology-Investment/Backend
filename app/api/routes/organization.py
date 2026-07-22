from __future__ import annotations

import sqlite3
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.deps.auth import require_permission
from app.database import get_db

router = APIRouter()


@router.get("/api/v1/organizations")
async def list_organizations(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: sqlite3.Connection = Depends(get_db),
):
    """Read-only, không gắn permission riêng — mọi user nội bộ đều cần xem
    được danh sách tổ chức (vd để hiển thị dropdown chọn org khi tạo user)."""
    offset = (page - 1) * limit
    rows = db.execute(
        "SELECT * FROM organizations ORDER BY created_at DESC LIMIT ? OFFSET ?", [limit, offset]
    ).fetchall()
    total = db.execute("SELECT COUNT(*) AS total FROM organizations").fetchone()["total"]
    return {"total": total, "organizations": [dict(r) for r in rows]}


@router.get("/api/v1/organizations/{organization_id}")
async def get_organization(organization_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM organizations WHERE organization_id = ?", (organization_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")
    return dict(row)


class CreateOrganizationRequest(BaseModel):
    organization_id: str
    name: str


@router.post("/api/v1/organizations", status_code=status.HTTP_201_CREATED)
async def create_organization(
    payload: CreateOrganizationRequest,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("system.org.create")),
):
    existing = db.execute(
        "SELECT organization_id FROM organizations WHERE organization_id = ?", (payload.organization_id,)
    ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail={"status": "ORG_ALREADY_EXISTS", "message": "Mã tổ chức đã tồn tại"})

    db.execute(
        "INSERT INTO organizations (organization_id, name, is_active) VALUES (?, ?, 1)",
        (payload.organization_id, payload.name),
    )
    db.commit()
    return {"organization_id": payload.organization_id, "name": payload.name, "is_active": True}


class UpdateOrganizationRequest(BaseModel):
    organization_id: Optional[str] = None
    name: Optional[str] = None
    is_active: Optional[bool] = None


@router.patch("/api/v1/organizations/{organization_id}")
async def update_organization(
    organization_id: str,
    payload: UpdateOrganizationRequest,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("system.org.update")),
):
    row = db.execute("SELECT * FROM organizations WHERE organization_id = ?", (organization_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")
    if payload.organization_id is not None:
        updates.append("organization_id = ?"); params.append(payload.organization_id)
    updates, params = [], []
    if payload.name is not None:
        updates.append("name = ?"); params.append(payload.name)
    if payload.is_active is not None:
        updates.append("is_active = ?"); params.append(1 if payload.is_active else 0)

    if updates:
        params.append(organization_id)
        db.execute(f"UPDATE organizations SET {', '.join(updates)} WHERE organization_id = ?", params)
        db.commit()

    row = db.execute("SELECT * FROM organizations WHERE organization_id = ?", (organization_id,)).fetchone()
    return dict(row)


@router.delete("/api/v1/organizations/{organization_id}")
async def delete_organization(
    organization_id: str,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("system.org.delete")),
):
    """Soft-delete (is_active=0). CHẶN nếu còn user đang active thuộc tổ chức
    này — xóa 'cứng' hoặc khóa 1 tổ chức còn user sẽ khiến user đó không login
    được (organizations.is_active bị check ở luồng SSO) một cách khó hiểu."""
    row = db.execute("SELECT organization_id FROM organizations WHERE organization_id = ?", (organization_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")

    active_users = db.execute(
        "SELECT COUNT(*) AS total FROM users WHERE organization_id = ? AND is_active = 1", (organization_id,)
    ).fetchone()["total"]
    if active_users > 0:
        raise HTTPException(
            status_code=409,
            detail={"status": "ORG_HAS_ACTIVE_USERS", "message": f"Còn {active_users} user đang hoạt động thuộc tổ chức này"},
        )

    db.execute("UPDATE organizations SET is_active = 0 WHERE organization_id = ?", (organization_id,))
    db.commit()
    return {"status": "SUCCESS", "message": "Đã khóa tổ chức"}