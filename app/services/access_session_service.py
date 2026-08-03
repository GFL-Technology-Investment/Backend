from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import HTTPException

from app.core.time import now_vn
from app.services.access_service import (
    build_session_detail,
    find_session_by_event_uid,
    get_latest_ticket_by_session_id,
    get_session_by_id,
    get_ticket_by_code,
    get_ticket_by_id,
    update_by_key,
)
from app.services.audit_service import write_audit_log


async def checkout_access_session(
    *,
    event_uid: Optional[str],
    ticket_code: Optional[str],
    note: Optional[str],
    db: sqlite3.Connection,
) -> dict:
    if not event_uid and not ticket_code:
        raise HTTPException(status_code=400, detail="Cần truyền event_uid hoặc ticket_code để checkout.")

    if note and len(note) > 500:
        raise HTTPException(status_code=400, detail="Ghi chú không được vượt quá 500 ký tự.")

    try:
        ticket = None
        if ticket_code:
            ticket = get_ticket_by_code(db, ticket_code)
            if not ticket:
                raise HTTPException(status_code=404, detail="Không tìm thấy ticket_code.")
            session = get_session_by_id(db, ticket["session_id"])
            if not session:
                raise HTTPException(status_code=404, detail="Không tìm thấy session của ticket.")
        else:
            session = find_session_by_event_uid(db, event_uid or "")

        if not session:
            raise HTTPException(status_code=404, detail="Không tìm thấy session để checkout.")

        lookup_uid = event_uid or session.get("event_uid") or session.get("linked_vehicle_event_uid")

        if session.get("status") == "CHECKED_OUT":
            return {
                "status": "ALREADY_CHECKED_OUT",
                "message": "Session đã checkout trước đó.",
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
            {"status": "CHECKED_OUT", "checked_out_at": current_time, "updated_at": current_time},
        )

        if ticket:
            update_by_key(
                db,
                "tickets",
                "ticket_id",
                ticket["ticket_id"],
                {"status": "CHECKED_OUT", "checked_out_at": current_time, "updated_at": current_time},
            )

        write_audit_log(
            db,
            "CHECK_OUT",
            session_id=session["session_id"],
            event_uid=lookup_uid,
            organization_id=session.get("organization_id"),
            gate_id=session.get("gate_id"),
            actor_type="GUARD",
            detail={"via": "ticket_code" if ticket_code else "event_uid", "note": note},
        )
        db.commit()

        return {
            "status": "SUCCESS",
            "message": "Đã checkout session.",
            "data": {
                "session": get_session_by_id(db, session["session_id"]),
                "ticket": get_ticket_by_id(db, ticket["ticket_id"]) if ticket else get_latest_ticket_by_session_id(db, session["session_id"]),
                "detail": build_session_detail(db, lookup_uid),
                "note": note,
            },
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
