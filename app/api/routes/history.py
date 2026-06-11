from __future__ import annotations
import asyncio
import os
import sqlite3
import uuid
from typing import Optional, Dict, Any, List, Tuple
from fastapi import APIRouter, UploadFile, File, Form, Request, HTTPException, Query, Depends
from app.database import get_db, DB_PATH
from app.core.config import settings
from app.services.access_service import *
from app.services.ocr_service import extract_cccd
from app.services.face_service import compare_face_image_paths, get_face_model_status

router = APIRouter()

@router.get("/api/v1/access/history")
async def get_access_history(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="WAITING_PERSON, WAITING_VEHICLE, WAITING_FACE_COMPARE, CHECKED_IN, CHECKED_OUT, NEED_REVIEW"),
    session_type: Optional[str] = Query(None, description="VEHICLE_WITH_PERSON hoặc PERSON_ONLY"),
    plate_number: Optional[str] = Query(None, description="Tìm theo biển số"),
    cccd_number: Optional[str] = Query(None, description="Tìm theo số CCCD"),
    full_name: Optional[str] = Query(None, description="Tìm theo tên"),
    organization_id: Optional[str] = Query(None),
    gate_id: Optional[str] = Query(None),
    db: sqlite3.Connection = Depends(get_db),
):
    """Trả danh sách lịch sử ra/vào cho frontend từ SQLite DB."""
    join_sql, params, where_sql = build_history_where(
        status,
        session_type,
        plate_number,
        cccd_number,
        full_name,
        organization_id,
        gate_id,
    )

    count_sql = f"SELECT COUNT(DISTINCT s.session_id) AS total FROM access_sessions s {join_sql} {where_sql}"
    total = db.execute(count_sql, params).fetchone()["total"]

    offset = (page - 1) * limit
    data_sql = f"""
        SELECT DISTINCT s.*
        FROM access_sessions s
        {join_sql}
        {where_sql}
        ORDER BY s.created_at DESC
        LIMIT ? OFFSET ?
    """
    rows = db.execute(data_sql, params + [limit, offset]).fetchall()
    sessions = [dict(row) for row in rows]
    data = [build_history_item(db, session) for session in sessions]

    summary_sql = f"""
        SELECT s.status, COUNT(DISTINCT s.session_id) AS count
        FROM access_sessions s
        {join_sql}
        {where_sql}
        GROUP BY s.status
    """
    summary_rows = db.execute(summary_sql, params).fetchall()
    status_counts = {row["status"]: row["count"] for row in summary_rows}

    total_sessions = db.execute("SELECT COUNT(*) AS total FROM access_sessions").fetchone()["total"]

    return {
        "status": "SUCCESS",
        "data": data,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": (total + limit - 1) // limit if limit else 0,
        },
        "summary": {
            "total_sessions": total_sessions,
            "total_filtered": total,
            "checked_in": status_counts.get("CHECKED_IN", 0),
            "checked_out": status_counts.get("CHECKED_OUT", 0),
            "need_review": status_counts.get("NEED_REVIEW", 0),
            "waiting": sum(count for status_key, count in status_counts.items() if str(status_key).startswith("WAITING")),
        },
    }


@router.get("/api/v1/access/history/{event_uid}")
async def get_access_history_detail(event_uid: str, db: sqlite3.Connection = Depends(get_db)):
    detail = build_session_detail(db, event_uid)
    session = detail.get("session")
    history_item = build_history_item(db, session)
    history_item["raw"] = detail

    return {
        "status": "SUCCESS",
        "data": history_item,
    }
