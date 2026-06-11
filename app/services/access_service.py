from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import uuid
from typing import Optional, Dict, Any, List, Tuple

from fastapi import HTTPException, Request, UploadFile

from app.core.config import settings
from app.core.time import now_vn
from app.core.files import static_url_to_local_path
from app.core.status import (
    ACTIVE_SESSION_STATUSES,
    STATUS_CHECKED_IN,
    STATUS_CHECKED_OUT,
    STATUS_REJECTED,
    STATUS_EXPIRED,
    LINK_POLICY_PERSON_ONLY_LOCKED,
)
from app.database import row_to_dict, DB_PATH
from app.services.ticket_renderer import render_ticket_images


# Runtime folders exported for route modules that currently use `from app.services.access_service import *`.
# Keep these aliases to avoid NameError after refactor.
UPLOAD_FOLDER = settings.upload_folder
STATIC_FOLDER = settings.static_folder
MEDIA_FOLDER = settings.media_folder
CCCD_ORIGINAL_FOLDER = settings.cccd_original_folder
CCCD_FACE_FOLDER = settings.cccd_face_folder
TICKETS_FOLDER = settings.tickets_folder


def normalize_plate_number(value: Optional[str]) -> Optional[str]:
    """Chuẩn hóa biển số để tránh tạo trùng do khác dấu cách/gạch ngang/chữ thường."""
    if value is None:
        return None
    normalized = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return normalized or None


def get_file_sha256(file_path: str) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def active_status_placeholders() -> str:
    return ",".join(["?"] * len(ACTIVE_SESSION_STATUSES))


def validate_required_text(value: Optional[str], field_name: str) -> str:
    if not value or not str(value).strip():
        raise HTTPException(status_code=400, detail=f"{field_name} không được để trống.")
    return str(value).strip()


def validate_cccd_ocr_result(result: Dict[str, Any]) -> None:
    # PoC cho phép OCR partial, nhưng phải có ít nhất CCCD hoặc họ tên để tạo person log.
    if not result.get("id") and not result.get("name"):
        raise HTTPException(
            status_code=422,
            detail="OCR không đọc được số CCCD hoặc họ tên. Vui lòng chụp lại ảnh rõ hơn.",
        )


def ensure_vehicle_event_uid_available(db: sqlite3.Connection, event_uid: str) -> Optional[Dict[str, Any]]:
    existing = get_vehicle_log(db, event_uid)
    if existing:
        session = get_session_by_id(db, existing["session_id"])
        return {
            "duplicate_type": "EVENT_UID",
            "session": session,
            "vehicle": existing,
            "person": get_person_log_by_session_id(db, existing["session_id"]),
        }
    return None


def find_active_vehicle_session_by_plate(
    db: sqlite3.Connection,
    plate_number: str,
    organization_id: Optional[str] = None,
    gate_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    plate = normalize_plate_number(plate_number)
    if not plate:
        return None

    params: List[Any] = [plate, *ACTIVE_SESSION_STATUSES]
    filters = [
        f"s.status IN ({active_status_placeholders()})",
        "REPLACE(REPLACE(UPPER(v.plate_number), '-', ''), ' ', '') = ?",
    ]
    # params phải theo thứ tự filter SQL; plate filter đang đứng sau status nên reorder bên dưới.
    params = [*ACTIVE_SESSION_STATUSES, plate]

    if organization_id:
        filters.append("s.organization_id = ?")
        params.append(organization_id)
    if gate_id:
        filters.append("s.gate_id = ?")
        params.append(gate_id)

    row = db.execute(
        f"""
        SELECT s.*
        FROM access_sessions s
        JOIN vehicle_access_logs v ON v.session_id = s.session_id
        WHERE {' AND '.join(filters)}
        ORDER BY s.created_at DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    return row_to_dict(row)


def find_waiting_vehicle_session_by_expected_plate(
    db: sqlite3.Connection,
    plate_number: str,
    organization_id: Optional[str] = None,
    gate_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    plate = normalize_plate_number(plate_number)
    if not plate:
        return None

    filters = [
        "s.session_type = 'VEHICLE_WITH_PERSON'",
        "s.status = 'WAITING_VEHICLE'",
        "s.linked_vehicle_event_uid IS NULL",
        "REPLACE(REPLACE(UPPER(s.expected_plate_number), '-', ''), ' ', '') = ?",
    ]
    params: List[Any] = [plate]
    if organization_id:
        filters.append("s.organization_id = ?")
        params.append(organization_id)
    if gate_id:
        filters.append("s.gate_id = ?")
        params.append(gate_id)

    row = db.execute(
        f"""
        SELECT s.*
        FROM access_sessions s
        WHERE {' AND '.join(filters)}
        ORDER BY s.created_at DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    return row_to_dict(row)


def find_recent_duplicate_vehicle_detection(
    db: sqlite3.Connection,
    plate_number: str,
    camera_id: Optional[str],
    gate_id: Optional[str],
    seconds: int = 15,
) -> Optional[Dict[str, Any]]:
    plate = normalize_plate_number(plate_number)
    if not plate:
        return None

    filters = [
        "REPLACE(REPLACE(UPPER(v.plate_number), '-', ''), ' ', '') = ?",
        "datetime(v.created_at) >= datetime('now', 'localtime', ?)",
    ]
    params: List[Any] = [plate, f"-{seconds} seconds"]
    if camera_id:
        filters.append("v.camera_id = ?")
        params.append(camera_id)
    if gate_id:
        filters.append("s.gate_id = ?")
        params.append(gate_id)

    row = db.execute(
        f"""
        SELECT s.*
        FROM vehicle_access_logs v
        JOIN access_sessions s ON s.session_id = v.session_id
        WHERE {' AND '.join(filters)}
        ORDER BY v.created_at DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    return row_to_dict(row)


def find_active_person_session_by_image_hash(db: sqlite3.Connection, image_hash: str) -> Optional[Dict[str, Any]]:
    row = db.execute(
        f"""
        SELECT s.*
        FROM person_access_logs p
        JOIN access_sessions s ON s.session_id = p.session_id
        WHERE p.cccd_image_hash = ?
          AND s.status IN ({active_status_placeholders()})
        ORDER BY p.created_at DESC
        LIMIT 1
        """,
        [image_hash, *ACTIVE_SESSION_STATUSES],
    ).fetchone()
    return row_to_dict(row)


def duplicate_response(kind: str, session: Dict[str, Any], db: sqlite3.Connection, message: str) -> Dict[str, Any]:
    event_uid = session.get("linked_vehicle_event_uid") or session.get("event_uid")
    return {
        "status": kind,
        "message": message,
        "data": build_session_detail(db, event_uid),
    }


def normalize_compare_result(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = value.strip().upper()
    allowed = {"MATCH", "NO_MATCH", "NEED_REVIEW"}
    if normalized not in allowed:
        raise HTTPException(status_code=400, detail="compare_result chỉ được là MATCH, NO_MATCH hoặc NEED_REVIEW")
    return normalized


def is_session_closed_or_final(session: Dict[str, Any]) -> bool:
    return session.get("status") in {STATUS_CHECKED_OUT, STATUS_REJECTED, STATUS_EXPIRED}


def assert_can_link_vehicle(session: Dict[str, Any]) -> None:
    if is_session_closed_or_final(session):
        raise HTTPException(status_code=400, detail="Session đã kết thúc hoặc bị từ chối/hết hạn, không thể ghép xe.")

    if session.get("link_policy") == LINK_POLICY_PERSON_ONLY_LOCKED:
        raise HTTPException(
            status_code=400,
            detail="PERSON_ONLY_SESSION_LOCKED: Phiên này đã được xác nhận là người không đi kèm xe, không thể ghép xe.",
        )

    if session.get("linked_vehicle_event_uid"):
        raise HTTPException(status_code=409, detail="Session này đã có xe được ghép, không thể ghép thêm xe khác.")


def assert_can_link_person_to_vehicle(session: Dict[str, Any]) -> None:
    if is_session_closed_or_final(session):
        raise HTTPException(status_code=400, detail="Session đã kết thúc hoặc bị từ chối/hết hạn, không thể ghép người.")

    if session.get("session_type") != "VEHICLE_WITH_PERSON":
        raise HTTPException(status_code=400, detail="Session xe phải có session_type = VEHICLE_WITH_PERSON mới được ghép người.")

    if session.get("status") != "WAITING_PERSON":
        raise HTTPException(status_code=400, detail="Chỉ session trạng thái WAITING_PERSON mới được OCR CCCD để ghép người vào xe.")


def safe_extension(filename: Optional[str], default: str = ".jpg") -> str:
    if not filename:
        return default
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        return default
    return ext


def save_upload_file(upload_file: UploadFile, folder: str, prefix: str = "file") -> str:
    os.makedirs(folder, exist_ok=True)
    ext = safe_extension(upload_file.filename)
    file_name = f"{prefix}-{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(folder, file_name)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)

    return file_path


def to_static_url(file_path: Optional[str]) -> Optional[str]:
    if not file_path:
        return None
    normalized = file_path.replace("\\", "/")
    if normalized.startswith("static/"):
        return "/" + normalized
    return normalized


def absolute_url(request: Request, url_path: Optional[str]) -> Optional[str]:
    if not url_path:
        return None
    if url_path.startswith("http://") or url_path.startswith("https://"):
        return url_path
    return str(request.base_url).rstrip("/") + url_path


def insert_row(db: sqlite3.Connection, table: str, data: Dict[str, Any]) -> None:
    keys = list(data.keys())
    placeholders = ", ".join(["?"] * len(keys))
    columns = ", ".join(keys)
    sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
    db.execute(sql, [data[k] for k in keys])


def upsert_row(db: sqlite3.Connection, table: str, data: Dict[str, Any]) -> None:
    keys = list(data.keys())
    placeholders = ", ".join(["?"] * len(keys))
    columns = ", ".join(keys)
    update_clause = ", ".join([f"{k}=excluded.{k}" for k in keys if k != "event_uid"])
    sql = f"""
        INSERT INTO {table} ({columns}) VALUES ({placeholders})
        ON CONFLICT(event_uid) DO UPDATE SET {update_clause}
    """
    db.execute(sql, [data[k] for k in keys])


def update_by_key(db: sqlite3.Connection, table: str, key_col: str, key_val: str, data: Dict[str, Any]) -> None:
    if not data:
        return
    keys = list(data.keys())
    set_clause = ", ".join([f"{k}=?" for k in keys])
    sql = f"UPDATE {table} SET {set_clause} WHERE {key_col}=?"
    db.execute(sql, [data[k] for k in keys] + [key_val])


def get_session_by_id(db: sqlite3.Connection, session_id: str) -> Optional[Dict[str, Any]]:
    row = db.execute("SELECT * FROM access_sessions WHERE session_id=?", (session_id,)).fetchone()
    return row_to_dict(row)


def get_vehicle_log(db: sqlite3.Connection, event_uid: str) -> Optional[Dict[str, Any]]:
    row = db.execute("SELECT * FROM vehicle_access_logs WHERE event_uid=?", (event_uid,)).fetchone()
    return row_to_dict(row)


def get_person_log(db: sqlite3.Connection, event_uid: str) -> Optional[Dict[str, Any]]:
    row = db.execute("SELECT * FROM person_access_logs WHERE event_uid=?", (event_uid,)).fetchone()
    return row_to_dict(row)


def get_vehicle_log_by_session_id(db: sqlite3.Connection, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    row = db.execute(
        "SELECT * FROM vehicle_access_logs WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return row_to_dict(row)


def get_person_log_by_session_id(db: sqlite3.Connection, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    row = db.execute(
        "SELECT * FROM person_access_logs WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return row_to_dict(row)


def find_session_by_event_uid(db: sqlite3.Connection, event_uid: str) -> Optional[Dict[str, Any]]:
    row = db.execute(
        """
        SELECT * FROM access_sessions
        WHERE event_uid=? OR linked_vehicle_event_uid=?
        LIMIT 1
        """,
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
    vehicle_log = get_vehicle_log(db, event_uid) or get_vehicle_log_by_session_id(db, session_id)
    person_log = get_person_log(db, event_uid) or get_person_log_by_session_id(db, session_id)

    return {"session": session, "vehicle": vehicle_log, "person": person_log}


def get_ticket_by_id(db: sqlite3.Connection, ticket_id: str) -> Optional[Dict[str, Any]]:
    row = db.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
    return row_to_dict(row)


def get_ticket_by_code(db: sqlite3.Connection, ticket_code: str) -> Optional[Dict[str, Any]]:
    row = db.execute("SELECT * FROM tickets WHERE ticket_code=?", (ticket_code,)).fetchone()
    return row_to_dict(row)


def get_latest_ticket_by_session_id(db: sqlite3.Connection, session_id: str) -> Optional[Dict[str, Any]]:
    row = db.execute(
        "SELECT * FROM tickets WHERE session_id=? ORDER BY issued_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return row_to_dict(row)


def build_ticket_payload(ticket: Dict[str, Any], session: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(ticket)
    if session:
        payload["session"] = session
    return payload


def issue_ticket_for_session(
    db: sqlite3.Connection,
    request: Request,
    session: Dict[str, Any],
    ticket_type: str = "VISITOR_PASS",
    issued_by: str = "SYSTEM",
    force_reissue: bool = False,
) -> Dict[str, Any]:
    if session.get("status") != "CHECKED_IN":
        raise HTTPException(status_code=400, detail="Chỉ tạo vé cho session đang CHECKED_IN.")

    existing = get_latest_ticket_by_session_id(db, session["session_id"])
    if existing and not force_reissue:
        return existing

    person_log = get_person_log_by_session_id(db, session["session_id"])
    vehicle_log = get_vehicle_log_by_session_id(db, session["session_id"])
    current_time = now_vn()
    ticket_id = f"TICKET-{uuid.uuid4().hex[:12]}"
    ticket_code = f"PTX-{uuid.uuid4().hex[:8].upper()}"

    rendered = render_ticket_images(
        request=request,
        ticket_code=ticket_code,
        ticket_id=ticket_id,
        session=session,
        person_log=person_log,
        vehicle_log=vehicle_log,
        ticket_type=ticket_type,
    )

    ticket_data = {
        "ticket_id": ticket_id,
        "session_id": session["session_id"],
        "ticket_code": ticket_code,
        "ticket_type": ticket_type,
        "front_image_url": rendered["front_image_url"],
        "back_image_url": rendered["back_image_url"],
        "qr_image_url": rendered["qr_image_url"],
        "barcode_image_url": rendered["barcode_image_url"],
        "qr_value": rendered.get("qr_value"),
        "barcode_value": rendered.get("barcode_value") or ticket_code,
        "status": "READY",
        "issued_by": issued_by,
        "issued_at": current_time,
        "printed_by": None,
        "printed_at": None,
        "checked_out_at": None,
        "created_at": current_time,
        "updated_at": current_time,
    }
    insert_row(db, "tickets", ticket_data)
    db.commit()
    return get_ticket_by_id(db, ticket_id) or ticket_data


def build_history_item(db: sqlite3.Connection, session: Dict[str, Any]) -> Dict[str, Any]:
    session_id = session.get("session_id")
    vehicle_log = get_vehicle_log_by_session_id(db, session_id)
    person_log = get_person_log_by_session_id(db, session_id)
    ticket = get_latest_ticket_by_session_id(db, session_id) if session_id else None

    primary_event_uid = (
        session.get("linked_vehicle_event_uid")
        or (vehicle_log or {}).get("event_uid")
        or session.get("event_uid")
        or (person_log or {}).get("event_uid")
    )

    detected_at = (
        (vehicle_log or {}).get("detected_at")
        or (person_log or {}).get("created_at")
        or session.get("created_at")
    )

    return {
        "session_id": session_id,
        "session_code": session.get("session_code"),
        "event_uid": primary_event_uid,
        "person_event_uid": (person_log or {}).get("event_uid"),
        "vehicle_event_uid": (vehicle_log or {}).get("event_uid"),
        "session_type": session.get("session_type"),
        "status": session.get("status"),
        "link_policy": session.get("link_policy"),
        "organization_id": session.get("organization_id"),
        "location_id": session.get("location_id"),
        "gate_id": session.get("gate_id"),
        "gate_name": session.get("gate_name"),
        "detected_at": detected_at,
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "checked_in_at": session.get("checked_in_at"),
        "checked_out_at": session.get("checked_out_at"),
        "plate": {
            "number": (vehicle_log or {}).get("plate_number") or session.get("expected_plate_number"),
            "confidence": (vehicle_log or {}).get("plate_confidence"),
            "plate_image_url": (vehicle_log or {}).get("plate_image_url"),
            "frame_image_url": (vehicle_log or {}).get("frame_image_url"),
            "driver_face_image_url": (vehicle_log or {}).get("driver_face_image_url"),
        },
        "person": {
            "cccd_number": (person_log or {}).get("cccd_number") or session.get("cccd_number"),
            "full_name": (person_log or {}).get("full_name") or session.get("full_name"),
            "birth": (person_log or {}).get("birth"),
            "sex": (person_log or {}).get("sex"),
            "place": (person_log or {}).get("place"),
            "cccd_face_image_url": (person_log or {}).get("cccd_face_image_url"),
            "cccd_original_image_url": (person_log or {}).get("cccd_original_image_url"),
            "live_face_image_url": (person_log or {}).get("live_face_image_url"),
            "live_face_source": (person_log or {}).get("live_face_source"),
        },
        "face_compare": {
            "source": (person_log or {}).get("face_compare_source"),
            "score": (person_log or {}).get("face_compare_score"),
            "threshold": (person_log or {}).get("face_compare_threshold"),
            "result": (person_log or {}).get("face_compare_result"),
        },
        "ticket": {
            "ticket_id": (ticket or {}).get("ticket_id"),
            "ticket_code": (ticket or {}).get("ticket_code"),
            "status": (ticket or {}).get("status"),
            "front_image_url": (ticket or {}).get("front_image_url"),
            "back_image_url": (ticket or {}).get("back_image_url"),
            "qr_image_url": (ticket or {}).get("qr_image_url"),
            "barcode_image_url": (ticket or {}).get("barcode_image_url"),
        },
    }


def build_history_where(
    status: Optional[str],
    session_type: Optional[str],
    plate_number: Optional[str],
    cccd_number: Optional[str],
    full_name: Optional[str],
    organization_id: Optional[str],
    gate_id: Optional[str],
) -> Tuple[str, List[Any], str]:
    joins = []
    conditions = []
    params: List[Any] = []

    if plate_number:
        joins.append("LEFT JOIN vehicle_access_logs v ON v.session_id = s.session_id")
        conditions.append("(v.plate_number LIKE ? OR s.expected_plate_number LIKE ?)")
        like = f"%{plate_number}%"
        params.extend([like, like])

    if cccd_number or full_name:
        joins.append("LEFT JOIN person_access_logs p ON p.session_id = s.session_id")
        if cccd_number:
            conditions.append("(p.cccd_number LIKE ? OR s.cccd_number LIKE ?)")
            like = f"%{cccd_number}%"
            params.extend([like, like])
        if full_name:
            conditions.append("(p.full_name LIKE ? OR s.full_name LIKE ?)")
            like = f"%{full_name}%"
            params.extend([like, like])

    if status:
        conditions.append("s.status = ?")
        params.append(status)
    if session_type:
        conditions.append("s.session_type = ?")
        params.append(session_type)
    if organization_id:
        conditions.append("s.organization_id = ?")
        params.append(organization_id)
    if gate_id:
        conditions.append("s.gate_id = ?")
        params.append(gate_id)

    join_sql = " ".join(dict.fromkeys(joins))
    where_sql = " WHERE " + " AND ".join(conditions) if conditions else ""
    return join_sql, params, where_sql
