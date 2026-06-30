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

@router.post("/api/v1/tickets/issue")
async def issue_ticket(
    request: Request,
    event_uid: Optional[str] = Form(None, description="event_uid LPR-... hoặc PERSON-..."),
    session_id: Optional[str] = Form(None),
    ticket_type: str = Form("VISITOR_PASS", description="VISITOR_PASS / VEHICLE_PASS / PERSON_ONLY_PASS"),
    issued_by: str = Form("guard-001"),
    force_reissue: bool = Form(False),
    db: sqlite3.Connection = Depends(get_db),
):
    if not event_uid and not session_id:
        raise HTTPException(status_code=400, detail="Cần truyền event_uid hoặc session_id để tạo vé.")

    session = get_session_by_id(db, session_id) if session_id else find_session_by_event_uid(db, event_uid or "")
    if not session:
        raise HTTPException(status_code=404, detail="Không tìm thấy session.")

    ticket = issue_ticket_for_session(
        db=db,
        request=request,
        session=session,
        ticket_type=ticket_type,
        issued_by=issued_by,
        force_reissue=force_reissue,
    )
    return {
        "status": "SUCCESS",
        "message": "Đã tạo vé. FE có thể mở front_image_url/back_image_url để preview.",
        "data": build_ticket_payload(ticket, get_session_by_id(db, session["session_id"])),
    }


@router.get("/api/v1/tickets")
async def list_tickets(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="READY / PRINTED / CHECKED_OUT"),
    db: sqlite3.Connection = Depends(get_db),
):
    conditions = []
    params: List[Any] = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    where_sql = " WHERE " + " AND ".join(conditions) if conditions else ""
    total = db.execute(f"SELECT COUNT(*) AS total FROM tickets{where_sql}", params).fetchone()["total"]
    offset = (page - 1) * limit
    rows = db.execute(
        f"SELECT * FROM tickets{where_sql} ORDER BY issued_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return {
        "status": "SUCCESS",
        "data": [dict(row) for row in rows],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": (total + limit - 1) // limit if limit else 0,
        },
    }


@router.post("/api/v1/tickets/checkout")
async def checkout_by_ticket_code(
    ticket_code: str = Form(..., description="Mã vé/barcode, ví dụ PTX-ABC12345"),
    db: sqlite3.Connection = Depends(get_db),
):
    ticket = get_ticket_by_code(db, ticket_code)
    if not ticket:
        raise HTTPException(status_code=404, detail="Không tìm thấy ticket_code.")
    session = get_session_by_id(db, ticket["session_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Không tìm thấy session của vé.")
    if session.get("status") == "CHECKED_OUT":
        return {
            "status": "ALREADY_CHECKED_OUT",
            "message": "Ticket/session đã checkout trước đó. Không cập nhật lại.",
            "data": {
                "session": session,
                "ticket": ticket,
                "person": get_person_log(db, session.get("event_uid")),
            },
        }
    if session.get("status") != "CHECKED_IN":
        raise HTTPException(status_code=400, detail="Chỉ ticket của session CHECKED_IN mới checkout được.")

    current_time = now_vn()
    update_by_key(db, "access_sessions", "session_id", session["session_id"], {
        "status": "CHECKED_OUT",
        "checked_out_at": current_time,
        "updated_at": current_time,
    })
    update_by_key(db, "tickets", "ticket_id", ticket["ticket_id"], {
        "status": "CHECKED_OUT",
        "checked_out_at": current_time,
        "updated_at": current_time,
    })
    db.commit()

    return {
        "status": "SUCCESS",
        "message": "Đã checkout bằng ticket_code.",
        "data": {
            "session": get_session_by_id(db, session["session_id"]),
            "person": get_person_log(db, session["event_uid"]),
            "ticket": get_ticket_by_id(db, ticket["ticket_id"]),
        },
    }


@router.get("/api/v1/tickets/verify/{ticket_code}")
async def verify_ticket(ticket_code: str, db: sqlite3.Connection = Depends(get_db)):
    """Endpoint dành cho QR Code.

    QR trên vé chứa URL này để FE/mobile scan có thể tra thông tin vé.
    Endpoint này chỉ xác thực và trả thông tin; checkout thật vẫn qua POST /api/v1/tickets/checkout.
    """
    ticket = get_ticket_by_code(db, ticket_code)
    if not ticket:
        raise HTTPException(status_code=404, detail="Không tìm thấy ticket_code.")
    session = get_session_by_id(db, ticket["session_id"])
    detail = build_session_detail(db, session.get("event_uid") if session else ticket.get("session_id")) if session else None
    return {
        "status": "SUCCESS",
        "data": {
            "ticket": ticket,
            "session": session,
            "detail": detail,
            "can_checkout": bool(session and session.get("status") == "CHECKED_IN"),
        },
    }


@router.get("/api/v1/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, db: sqlite3.Connection = Depends(get_db)):
    ticket = get_ticket_by_id(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Không tìm thấy ticket_id.")
    session = get_session_by_id(db, ticket["session_id"])
    return {
        "status": "SUCCESS",
        "data": build_ticket_payload(ticket, session),
    }


@router.post("/api/v1/tickets/{ticket_id}/print")
async def print_ticket(
    ticket_id: str,
    printer_name: str = Form("MOCK_PRINTER"),
    printed_by: str = Form("guard-001"),
    db: sqlite3.Connection = Depends(get_db),
):
    ticket = get_ticket_by_id(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Không tìm thấy ticket_id.")
    if ticket.get("status") == "CHECKED_OUT":
        raise HTTPException(status_code=400, detail="Vé đã CHECKED_OUT, không thể in lại trong bản mock.")

    current_time = now_vn()
    update_by_key(db, "tickets", "ticket_id", ticket_id, {
        "status": "PRINTED",
        "printed_by": printed_by,
        "printed_at": current_time,
        "updated_at": current_time,
    })
    insert_row(db, "ticket_print_logs", {
        "ticket_id": ticket_id,
        "printer_name": printer_name,
        "print_status": "SUCCESS",
        "printed_by": printed_by,
        "printed_at": current_time,
        "error_message": None,
    })
    db.commit()

    return {
        "status": "SUCCESS",
        "message": "Đã giả lập in vé. Bản thật sẽ thay bằng ESC/POS, Windows printer hoặc CUPS.",
        "data": {
            "ticket": get_ticket_by_id(db, ticket_id),
            "print_logs": [dict(row) for row in db.execute(
                "SELECT * FROM ticket_print_logs WHERE ticket_id=? ORDER BY printed_at DESC",
                (ticket_id,),
            ).fetchall()],
        },
    }

