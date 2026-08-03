from __future__ import annotations

import sqlite3
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps.auth import require_permission
from app.database import get_db

router = APIRouter()


class UpdatePermissionRequest(BaseModel):
    permission_name: Optional[str] = None
    description: Optional[str] = None


class CreatePermissionRequest(BaseModel):
    permission_code: str
    permission_name: Optional[str] = None
    module_name: Optional[str] = None
    description: Optional[str] = None


@router.get("/api/v1/permissions")
async def list_permissions(
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("permission.read")),
):
    rows = db.execute("SELECT permission_id, permission_code, permission_name, module_name FROM permissions ORDER BY module_name, permission_code").fetchall()
    return {"permissions": [dict(r) for r in rows]}


@router.get("/api/v1/permissions/{permission_id}")
async def get_permission(
    permission_id: str,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("permission.read")),
):
    row = db.execute("SELECT * FROM permissions WHERE permission_id = ?", (permission_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Permission not found")
    return dict(row)


@router.post("/api/v1/permissions", status_code=status.HTTP_201_CREATED)
async def create_permission(
    payload: CreatePermissionRequest,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("permission.create")),
):
    existing = db.execute("SELECT permission_id FROM permissions WHERE permission_code = ?", (payload.permission_code,)).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="Permission already exists")

    permission_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO permissions (permission_id, permission_code, permission_name, module_name, description) VALUES (?, ?, ?, ?, ?)",
        (permission_id, payload.permission_code, payload.permission_name, payload.module_name, payload.description),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM permissions WHERE permission_id = ?", (permission_id,)).fetchone())


@router.patch("/api/v1/permissions/{permission_id}")
async def update_permission(
    permission_id: str,
    payload: UpdatePermissionRequest,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("permission.update")),
):
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


@router.delete("/api/v1/permissions/{permission_id}")
async def delete_permission(
    permission_id: str,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("permission.delete")),
):
    row = db.execute("SELECT permission_id FROM permissions WHERE permission_id = ?", (permission_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Permission not found")

    assigned = db.execute("SELECT COUNT(*) AS total FROM role_permissions WHERE permission_id = ?", (permission_id,)).fetchone()["total"]
    if assigned:
        raise HTTPException(status_code=409, detail="Permission is assigned to one or more roles")

    db.execute("DELETE FROM permissions WHERE permission_id = ?", (permission_id,))
    db.commit()
    return {"status": "SUCCESS", "permission_id": permission_id}
