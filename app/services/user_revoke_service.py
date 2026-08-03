from __future__ import annotations

import sqlite3

from app.services.session_service import delete_sessions


async def revoke_user_sessions(db: sqlite3.Connection, user_id: str) -> None:
    session_rows = db.execute(
        "SELECT DISTINCT session_id FROM refresh_tokens WHERE user_id = ? AND session_id IS NOT NULL",
        (user_id,),
    ).fetchall()
    session_ids = [row["session_id"] for row in session_rows]

    db.execute("UPDATE users SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", (user_id,))
    db.execute("UPDATE refresh_tokens SET is_revoked = 1 WHERE user_id = ?", (user_id,))
    db.commit()

    await delete_sessions(session_ids)
