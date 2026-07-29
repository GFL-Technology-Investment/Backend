from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import HTTPException, status


def link_card_to_session(
    db: sqlite3.Connection,
    card_id: str,
    session_id: str,
    organization_id: Optional[str] = None,
) -> None:
    """Gắn thẻ với phiên, chỉ gắn được khi phiên đang CHECKED_IN và thẻ chưa được gắn cho phiên khác"""
    session_row = db.execute(
        "SELECT status FROM access_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not session_row:
        raise HTTPException(status_code=404, detail={"status": "SESSION_NOT_FOUND", "message": "Không tìm thấy phiên"})
    if session_row["status"] != "CHECKED_IN":
        raise HTTPException(
            status_code=409,
            detail={"status": "SESSION_NOT_CHECKED_IN", "message": "Chỉ gắn thẻ được khi phiên đã CHECKED_IN"},
        )

    card_row = db.execute("SELECT * FROM access_cards WHERE card_id = ?", (card_id,)).fetchone()

    if card_row is None:
        # Lần đầu thấy UID này -> tự đăng ký, gắn luôn
        db.execute(
            """
            INSERT INTO access_cards (card_id, status, session_id, organization_id, linked_at, updated_at)
            VALUES (?, 'IN_USE', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (card_id, session_id, organization_id),
        )
        return

    if card_row["status"] == "IN_USE":
        raise HTTPException(
            status_code=409,
            detail={"status": "CARD_ALREADY_IN_USE", "message": "Thẻ này đang được gắn cho 1 phiên khác — dùng thẻ khác"},
        )
    if card_row["status"] == "DISABLED":
        raise HTTPException(status_code=409, detail={"status": "CARD_DISABLED", "message": "Thẻ đã bị khóa, không dùng được"})

    db.execute(
        """
        UPDATE access_cards
        SET status = 'IN_USE', session_id = ?, organization_id = ?, linked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE card_id = ?
        """,
        (session_id, organization_id, card_id),
    )


def get_session_id_by_card(db: sqlite3.Connection, card_id: str) -> str:
    card_row = db.execute("SELECT * FROM access_cards WHERE card_id = ?", (card_id,)).fetchone()
    if not card_row or card_row["status"] != "IN_USE" or not card_row["session_id"]:
        raise HTTPException(
            status_code=404,
            detail={"status": "CARD_NOT_IN_USE", "message": "Thẻ này chưa được gắn với phiên nào"},
        )
    return card_row["session_id"]


def reset_card(db: sqlite3.Connection, card_id: str) -> None:
    """Reset thẻ về trạng thái AVAILABLE, xóa session_id"""
    db.execute(
        "UPDATE access_cards SET status = 'AVAILABLE', session_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE card_id = ?",
        (card_id,),
    )