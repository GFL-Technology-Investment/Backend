from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, Form

from app.database import DB_PATH, get_db
from app.services.access_service import build_session_detail
from app.services.access_session_service import checkout_access_session as checkout_access_session_service

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
    return {"status": "SUCCESS", "data": build_session_detail(db, event_uid)}


@router.post("/api/v1/access/checkout")
async def checkout_access_session(
    event_uid: Optional[str] = Form(None, description="event_uid LPR-... hoặc PERSON-..."),
    ticket_code: Optional[str] = Form(None, description="Có thể checkout bằng mã vé/barcode"),
    note: Optional[str] = Form(None),
    db: sqlite3.Connection = Depends(get_db),
):
    return await checkout_access_session_service(
        event_uid=event_uid,
        ticket_code=ticket_code,
        note=note,
        db=db,
    )
