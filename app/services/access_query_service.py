from typing import Any, Dict, Optional
import sqlite3

from fastapi import HTTPException

from app.database import row_to_dict


def insert_row(db: sqlite3.Connection, table: str, data: Dict[str, Any]) -> None:
    keys = list(data.keys())
    placeholders = ", ".join(["?"] * len(keys))
    columns = ", ".join(keys)
    db.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", [data[k] for k in keys])


def upsert_row(db: sqlite3.Connection, table: str, data: Dict[str, Any]) -> None:
    keys = list(data.keys())
    placeholders = ", ".join(["?"] * len(keys))
    columns = ", ".join(keys)
    update_clause = ", ".join([f"{k}=excluded.{k}" for k in keys if k != "event_uid"])
    db.execute(
        f"""INSERT INTO {table} ({columns}) VALUES ({placeholders})
        ON CONFLICT(event_uid) DO UPDATE SET {update_clause}""",
        [data[k] for k in keys],
    )


def update_by_key(db: sqlite3.Connection, table: str, key_col: str, key_val: str, data: Dict[str, Any]) -> None:
    if data:
        keys = list(data.keys())
        db.execute(
            f"UPDATE {table} SET {', '.join([f'{k}=?' for k in keys])} WHERE {key_col}=?",
            [data[k] for k in keys] + [key_val],
        )


def get_session_by_id(db: sqlite3.Connection, session_id: str) -> Optional[Dict[str, Any]]:
    return row_to_dict(db.execute("SELECT * FROM access_sessions WHERE session_id=?", (session_id,)).fetchone())


def get_vehicle_log(db: sqlite3.Connection, event_uid: str) -> Optional[Dict[str, Any]]:
    return row_to_dict(db.execute("SELECT * FROM vehicle_access_logs WHERE event_uid=?", (event_uid,)).fetchone())


def get_person_log(db: sqlite3.Connection, event_uid: str) -> Optional[Dict[str, Any]]:
    return row_to_dict(db.execute("SELECT * FROM person_access_logs WHERE event_uid=?", (event_uid,)).fetchone())


def get_vehicle_log_by_session_id(db: sqlite3.Connection, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    return row_to_dict(db.execute(
        "SELECT * FROM vehicle_access_logs WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    ).fetchone())


def get_person_log_by_session_id(db: sqlite3.Connection, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    return row_to_dict(db.execute(
        "SELECT * FROM person_access_logs WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    ).fetchone())


def find_session_by_event_uid(db: sqlite3.Connection, event_uid: str) -> Optional[Dict[str, Any]]:
    row = db.execute(
        "SELECT * FROM access_sessions WHERE event_uid=? OR linked_vehicle_event_uid=? LIMIT 1",
        (event_uid, event_uid),
    ).fetchone()
    if row:
        return row_to_dict(row)
    vehicle = get_vehicle_log(db, event_uid)
    if vehicle:
        return get_session_by_id(db, vehicle["session_id"])
    person = get_person_log(db, event_uid)
    if person:
        return get_session_by_id(db, person["session_id"])
    return None


def build_session_detail(db: sqlite3.Connection, event_uid: str) -> Dict[str, Any]:
    session = find_session_by_event_uid(db, event_uid)
    if not session:
        raise HTTPException(status_code=404, detail="Không tìm thấy event_uid/session")
    session_id = session.get("session_id")
    return {
        "session": session,
        "vehicle": get_vehicle_log(db, event_uid) or get_vehicle_log_by_session_id(db, session_id),
        "person": get_person_log(db, event_uid) or get_person_log_by_session_id(db, session_id),
    }


def get_ticket_by_id(db: sqlite3.Connection, ticket_id: str) -> Optional[Dict[str, Any]]:
    return row_to_dict(db.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone())


def get_ticket_by_code(db: sqlite3.Connection, ticket_code: str) -> Optional[Dict[str, Any]]:
    return row_to_dict(db.execute("SELECT * FROM tickets WHERE ticket_code=?", (ticket_code,)).fetchone())


def get_latest_ticket_by_session_id(db: sqlite3.Connection, session_id: str) -> Optional[Dict[str, Any]]:
    return row_to_dict(db.execute(
        "SELECT * FROM tickets WHERE session_id=? ORDER BY issued_at DESC LIMIT 1",
        (session_id,),
    ).fetchone())


def build_ticket_payload(ticket: Dict[str, Any], session: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(ticket)
    if session:
        payload["session"] = session
    return payload
