from __future__ import annotations

import sqlite3
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps.auth import require_permission
from app.database import get_db

router = APIRouter()


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
