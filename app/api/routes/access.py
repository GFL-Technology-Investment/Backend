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

@router.get("/mock/access-sessions")
async def list_mock_sessions(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM access_sessions ORDER BY created_at DESC").fetchall()
    data = [dict(row) for row in rows]
    return {
        "status": "SUCCESS",
        "data": data,
        "total": len(data),
        "storage": "sqlite",
        "database": DB_PATH,
        "time_format": "Asia/Ho_Chi_Minh - YYYY-MM-DD HH:mm:ss",
    }


@router.get("/mock/access-sessions/{event_uid}")
async def get_mock_session(event_uid: str, db: sqlite3.Connection = Depends(get_db)):
    return {
        "status": "SUCCESS",
        "data": build_session_detail(db, event_uid),
    }

# -----------------------------------------------------------------------------
# 7) API checkout chung: dùng được cho cả VEHICLE_WITH_PERSON và PERSON_ONLY
# -----------------------------------------------------------------------------
@router.post("/api/v1/access/checkout")
async def checkout_access_session(
    event_uid: Optional[str] = Form(None, description="event_uid LPR-... hoặc PERSON-..."),
    ticket_code: Optional[str] = Form(None, description="Có thể checkout bằng mã vé/barcode"),
    note: Optional[str] = Form(None),
    db: sqlite3.Connection = Depends(get_db),
):
    if not event_uid and not ticket_code:
        raise HTTPException(status_code=400, detail="Cần truyền event_uid hoặc ticket_code để checkout.")

    ticket = None
    if ticket_code:
        ticket = get_ticket_by_code(db, ticket_code)
        if not ticket:
            raise HTTPException(status_code=404, detail="Không tìm thấy ticket_code.")
        session = get_session_by_id(db, ticket["session_id"])
    else:
        session = find_session_by_event_uid(db, event_uid or "")

    if not session:
        raise HTTPException(status_code=404, detail="Không tìm thấy session để checkout.")

    if session.get("status") == "CHECKED_OUT":
        lookup_uid = event_uid or session.get("event_uid") or session.get("linked_vehicle_event_uid")
        return {
            "status": "ALREADY_CHECKED_OUT",
            "message": "Session đã checkout trước đó. Không cập nhật lại.",
            "data": {
                "session": session,
                "ticket": ticket or get_latest_ticket_by_session_id(db, session["session_id"]),
                "detail": build_session_detail(db, lookup_uid),
                "note": note,
            },
        }
    if session.get("status") != "CHECKED_IN":
        raise HTTPException(status_code=400, detail="Chỉ session trạng thái CHECKED_IN mới được checkout.")

    current_time = now_vn()
    update_by_key(
        db,
        "access_sessions",
        "session_id",
        session["session_id"],
        {
            "status": "CHECKED_OUT",
            "checked_out_at": current_time,
            "updated_at": current_time,
        },
    )

    if ticket:
        update_by_key(
            db,
            "tickets",
            "ticket_id",
            ticket["ticket_id"],
            {
                "status": "CHECKED_OUT",
                "checked_out_at": current_time,
                "updated_at": current_time,
            },
        )

    db.commit()

    lookup_uid = event_uid or session.get("event_uid") or session.get("linked_vehicle_event_uid")
    return {
        "status": "SUCCESS",
        "message": "Đã checkout session, status = CHECKED_OUT.",
        "data": {
            "session": get_session_by_id(db, session["session_id"]),
            "ticket": get_ticket_by_id(db, ticket["ticket_id"]) if ticket else get_latest_ticket_by_session_id(db, session["session_id"]),
            "detail": build_session_detail(db, lookup_uid),
            "note": note,
        },
    }
