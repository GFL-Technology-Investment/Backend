from __future__ import annotations
import json
import sqlite3
from typing import Any
from fastapi import APIRouter, UploadFile, File, Form, Request, HTTPException, Query, Depends

from app.database import row_to_dict, get_db
router = APIRouter()
@router.get("/api/v1/list/user")
async def get_list_user(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: sqlite3.Connection = Depends(get_db),
):
    """Trả danh sách người dùng cho frontend từ SQLite DB."""
    offset = (page - 1) * limit
    data_sql = f"""
        SELECT *
        FROM users
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """
    rows = db.execute(data_sql, [limit, offset]).fetchall()
    users = [row_to_dict(row) for row in rows]

    count_sql = "SELECT COUNT(*) AS total FROM users"
    total = db.execute(count_sql).fetchone()["total"]

    return {"total": total, "users": users}

@router.get("/api/v1/user/{user_id}")
async def get_user_by_id(
    user_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """Trả thông tin người dùng theo user_id cho frontend từ SQLite DB."""
    data_sql = "SELECT * FROM users WHERE user_id = ?"
    row = db.execute(data_sql, [user_id]).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    user = row_to_dict(row)
    return user
