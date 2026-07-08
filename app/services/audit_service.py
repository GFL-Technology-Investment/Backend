from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Dict, Optional

from app.core.time import now_vn


def write_audit_log(
    db: sqlite3.Connection,
    event_type: str,
    *,
    session_id: Optional[str] = None,
    event_uid: Optional[str] = None,
    organization_id: Optional[str] = None,
    gate_id: Optional[str] = None,
    actor_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    result_status: str = "SUCCESS",
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        db.execute(
            """
            INSERT INTO audit_logs (
                audit_log_id, event_type, session_id, event_uid,
                organization_id, gate_id, actor_type, actor_id,
                result_status, detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"AUDIT-{uuid.uuid4().hex[:16]}",
                event_type,
                session_id,
                event_uid,
                organization_id,
                gate_id,
                actor_type,
                actor_id,
                result_status,
                json.dumps(detail or {}, ensure_ascii=False),
                now_vn(),
            ),
        )
    except Exception as exc:  
        print(f"[audit_service] Ghi audit log thất bại (event_type={event_type}): {exc}")