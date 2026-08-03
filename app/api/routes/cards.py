from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps.auth import require_permission
from app.database import get_db
from app.services import card_service
from app.services.access_session_service import checkout_access_session
from app.services.access_service import get_session_by_id

router = APIRouter()


class LinkCardRequest(BaseModel):
    card_id: str
    session_id: str
    organization_id: Optional[str] = None


@router.post("/api/v1/cards/link")
async def link_card(
    payload: LinkCardRequest,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("card.link")),
):
    card_service.link_card_to_session(db, payload.card_id, payload.session_id, payload.organization_id)
    db.commit()
    return {
        "status": "SUCCESS",
        "message": "Đã gắn thẻ vào phiên",
        "card_id": payload.card_id,
        "session_id": payload.session_id,
    }


class CardCheckoutRequest(BaseModel):
    card_id: str
    note: Optional[str] = None


@router.post("/api/v1/cards/checkout")
async def checkout_by_card(
    payload: CardCheckoutRequest,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("card.checkout")),
):
    session_id = card_service.get_session_id_by_card(db, payload.card_id)

    session = get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail={"status": "SESSION_NOT_FOUND", "message": "Không tìm thấy phiên gắn với thẻ này"},
        )

    event_uid = session.get("event_uid") or session.get("linked_vehicle_event_uid")

    result = await checkout_access_session(event_uid=event_uid, ticket_code=None, note=payload.note, db=db)

    # Checkout xong (dù SUCCESS hay ALREADY_CHECKED_OUT) -> trả thẻ về pool
    # ngay, sẵn sàng cho xe tiếp theo mà không cần thao tác gì thêm.
    card_service.reset_card(db, payload.card_id)
    db.commit()

    result["card_id"] = payload.card_id
    result["card_status"] = "AVAILABLE"
    return result


@router.get("/api/v1/cards")
async def list_cards(
    status_filter: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db),
    _auth=Depends(require_permission("card.manage")),
):
    if status_filter:
        rows = db.execute(
            "SELECT * FROM access_cards WHERE status = ? ORDER BY updated_at DESC", (status_filter,)
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM access_cards ORDER BY updated_at DESC").fetchall()
    return {"cards": [dict(r) for r in rows]}
