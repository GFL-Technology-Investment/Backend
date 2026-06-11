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

@router.post("/mock/aibox/lpr-event")
async def mock_aibox_lpr_event(
    request: Request,
    event_uid: Optional[str] = Form(None, description="event_uid tùy chọn để test retry duplicate từ camera/AIBox"),
    plate_number: str = Form(..., description="Biển số xe, ví dụ 30A12345"),
    organization_id: str = Form("org-001"),
    location_id: str = Form("loc-001"),
    gate_id: str = Form("gate-001"),
    gate_name: str = Form("Cổng vào 01"),
    camera_id: str = Form("cam-gate-01"),
    camera_name: str = Form("Camera cổng vào 01"),
    plate_image: UploadFile = File(..., description="Ảnh crop biển số"),
    frame_image: UploadFile = File(..., description="Ảnh toàn cảnh"),
    driver_face_image: Optional[UploadFile] = File(None, description="Ảnh mặt tài xế từ camera nếu có"),
    db: sqlite3.Connection = Depends(get_db),
):
    plate_number = normalize_plate_number(validate_required_text(plate_number, "plate_number"))
    event_uid = event_uid or f"LPR-{uuid.uuid4().hex[:12]}"
    current_time = now_vn()

    # 1) Idempotency theo event_uid: AIBox retry cùng event_uid thì trả lại dữ liệu cũ, không insert thêm.
    existing_event = ensure_vehicle_event_uid_available(db, event_uid)
    if existing_event:
        return {
            "status": "DUPLICATE_EVENT_UID",
            "message": "Event xe này đã tồn tại. Backend trả lại session cũ, không tạo bản ghi mới.",
            "data": existing_event,
        }

    # 2) Nếu người OCR trước và đang chờ xe theo expected_plate_number, ghép xe vào session đó.
    waiting_person_session = find_waiting_vehicle_session_by_expected_plate(db, plate_number, organization_id, gate_id)
    if waiting_person_session:
        person_log = get_person_log_by_session_id(db, waiting_person_session["session_id"])
        if not person_log:
            raise HTTPException(status_code=409, detail="Session WAITING_VEHICLE không có person log, cần kiểm tra dữ liệu.")

        plate_path = save_upload_file(plate_image, MEDIA_FOLDER, f"{event_uid}-plate")
        frame_path = save_upload_file(frame_image, MEDIA_FOLDER, f"{event_uid}-frame")
        driver_face_url = None
        if driver_face_image:
            driver_face_path = save_upload_file(driver_face_image, MEDIA_FOLDER, f"{event_uid}-driver-face")
            driver_face_url = absolute_url(request, to_static_url(driver_face_path))
            update_by_key(db, "person_access_logs", "event_uid", person_log["event_uid"], {
                "live_face_image_url": driver_face_url,
                "live_face_source": "CAMERA",
                "updated_at": current_time,
            })

        vehicle_data = {
            "event_uid": event_uid,
            "session_id": waiting_person_session["session_id"],
            "event_type": "VEHICLE_ACCESS",
            "source": "AIBOX_MOCK",
            "plate_number": plate_number,
            "plate_confidence": None,
            "camera_id": camera_id,
            "camera_name": camera_name,
            "device_serial_number": None,
            "plate_image_url": absolute_url(request, to_static_url(plate_path)),
            "frame_image_url": absolute_url(request, to_static_url(frame_path)),
            "driver_face_image_url": driver_face_url,
            "video_url": None,
            "detected_at": current_time,
            "created_at": current_time,
            "updated_at": current_time,
        }
        try:
            insert_row(db, "vehicle_access_logs", vehicle_data)
            update_by_key(db, "access_sessions", "session_id", waiting_person_session["session_id"], {
                "linked_vehicle_event_uid": event_uid,
                "session_type": "VEHICLE_WITH_PERSON",
                "link_policy": "ALLOW_VEHICLE_LINK",
                "status": "WAITING_FACE_COMPARE",
                "updated_at": current_time,
            })
            db.commit()
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail=f"Lỗi trùng dữ liệu khi tự ghép xe vào session WAITING_VEHICLE: {exc}")

        updated_session = get_session_by_id(db, waiting_person_session["session_id"])
        return {
            "status": "LINKED_TO_WAITING_PERSON",
            "message": "Xe khớp expected_plate_number, đã tự ghép vào session người đang WAITING_VEHICLE.",
            "data": {
                "event_uid": event_uid,
                "session": updated_session,
                "vehicle": get_vehicle_log(db, event_uid),
                "person": get_person_log_by_session_id(db, waiting_person_session["session_id"]),
            },
        }

    # 3) Chống camera gửi nhiều frame cùng một xe/cổng khi session cũ còn active.
    active_session = find_active_vehicle_session_by_plate(db, plate_number, organization_id, gate_id)
    if active_session:
        return duplicate_response(
            "DUPLICATE_ACTIVE_VEHICLE_SESSION",
            active_session,
            db,
            "Xe này đang có session active tại cùng tổ chức/cổng. Không tạo session mới.",
        )

    recent_duplicate = find_recent_duplicate_vehicle_detection(db, plate_number, camera_id, gate_id, seconds=15)
    if recent_duplicate:
        return duplicate_response(
            "DUPLICATE_RECENT_DETECTION",
            recent_duplicate,
            db,
            "Camera vừa gửi event cùng biển số trong thời gian rất gần. Không tạo session mới.",
        )

    # 4) Không trùng -> tạo session xe đến trước, chờ người OCR.
    session_id = f"SESSION-{uuid.uuid4().hex[:12]}"
    plate_path = save_upload_file(plate_image, MEDIA_FOLDER, f"{event_uid}-plate")
    frame_path = save_upload_file(frame_image, MEDIA_FOLDER, f"{event_uid}-frame")

    driver_face_url = None
    if driver_face_image:
        driver_face_path = save_upload_file(driver_face_image, MEDIA_FOLDER, f"{event_uid}-driver-face")
        driver_face_url = absolute_url(request, to_static_url(driver_face_path))

    session_data = {
        "session_id": session_id,
        "session_code": session_id,
        "event_uid": event_uid,
        "linked_vehicle_event_uid": None,
        "session_type": "VEHICLE_WITH_PERSON",
        "organization_id": organization_id,
        "location_id": location_id,
        "gate_id": gate_id,
        "gate_name": gate_name,
        "status": "WAITING_PERSON",
        "link_policy": "ALLOW_VEHICLE_LINK",
        "expected_plate_number": plate_number,
        "cccd_number": None,
        "full_name": None,
        "checked_in_at": None,
        "checked_out_at": None,
        "created_at": current_time,
        "updated_at": current_time,
    }

    vehicle_data = {
        "event_uid": event_uid,
        "session_id": session_id,
        "event_type": "VEHICLE_ACCESS",
        "source": "AIBOX_MOCK",
        "plate_number": plate_number,
        "plate_confidence": None,
        "camera_id": camera_id,
        "camera_name": camera_name,
        "device_serial_number": None,
        "plate_image_url": absolute_url(request, to_static_url(plate_path)),
        "frame_image_url": absolute_url(request, to_static_url(frame_path)),
        "driver_face_image_url": driver_face_url,
        "video_url": None,
        "detected_at": current_time,
        "created_at": current_time,
        "updated_at": current_time,
    }

    try:
        insert_row(db, "access_sessions", session_data)
        insert_row(db, "vehicle_access_logs", vehicle_data)
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Lỗi trùng dữ liệu khi tạo session/event: {exc}")

    return {
        "status": "SUCCESS",
        "message": "Đã giả lập camera phát hiện xe. Session đã lưu DB và đang chờ người OCR CCCD.",
        "data": {
            "event_uid": event_uid,
            "session_id": session_id,
            "plate_number": plate_number,
            "session_status": "WAITING_PERSON",
        },
    }


# -----------------------------------------------------------------------------
# 2) OCR CCCD: có thể chạy độc lập hoặc ghép vào event xe bằng event_uid


@router.post("/mock/aibox/link-vehicle-to-person")
@router.post("/mock/aibox/link-vehicle-to-waiting-person")
async def link_vehicle_to_waiting_person(
    request: Request,
    person_event_uid: str = Form(..., description="event_uid dạng PERSON-... trả về từ /ocr/cccd khi người OCR trước"),
    plate_number: str = Form(...),
    plate_image: UploadFile = File(...),
    frame_image: UploadFile = File(...),
    driver_face_image: Optional[UploadFile] = File(None),
    db: sqlite3.Connection = Depends(get_db),
):
    plate_number = normalize_plate_number(validate_required_text(plate_number, "plate_number"))

    person_log = get_person_log(db, person_event_uid)
    if not person_log:
        raise HTTPException(status_code=404, detail="Không tìm thấy person_event_uid")

    session = get_session_by_id(db, person_log["session_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Không tìm thấy session của person_event_uid")

    assert_can_link_vehicle(session)

    existing_vehicle = get_vehicle_log_by_session_id(db, session["session_id"])
    if existing_vehicle:
        raise HTTPException(status_code=409, detail="Session này đã có vehicle_access_logs, không thể ghép thêm xe khác.")

    active_vehicle_session = find_active_vehicle_session_by_plate(
        db,
        plate_number,
        session.get("organization_id"),
        session.get("gate_id"),
    )
    if active_vehicle_session and active_vehicle_session.get("session_id") != session.get("session_id"):
        raise HTTPException(
            status_code=409,
            detail="Biển số này đang thuộc một session active khác, không thể ghép vào session người hiện tại.",
        )

    # Nếu session ban đầu là PERSON_ONLY nhưng chưa bị khóa, cho phép nâng cấp
    # sang VEHICLE_WITH_PERSON khi bảo vệ xác nhận có xe đến sau.
    expected_plate = session.get("expected_plate_number")
    status_after_link = "WAITING_FACE_COMPARE"
    if expected_plate and expected_plate != plate_number:
        status_after_link = "NEED_REVIEW"

    vehicle_event_uid = f"LPR-{uuid.uuid4().hex[:12]}"
    current_time = now_vn()

    plate_path = save_upload_file(plate_image, MEDIA_FOLDER, f"{vehicle_event_uid}-plate")
    frame_path = save_upload_file(frame_image, MEDIA_FOLDER, f"{vehicle_event_uid}-frame")

    driver_face_url = None
    if driver_face_image:
        driver_face_path = save_upload_file(driver_face_image, MEDIA_FOLDER, f"{vehicle_event_uid}-driver-face")
        driver_face_url = absolute_url(request, to_static_url(driver_face_path))
        update_by_key(
            db,
            "person_access_logs",
            "event_uid",
            person_event_uid,
            {
                "live_face_image_url": driver_face_url,
                "live_face_source": "CAMERA",
                "updated_at": current_time,
            },
        )

    vehicle_data = {
        "event_uid": vehicle_event_uid,
        "session_id": session["session_id"],
        "event_type": "VEHICLE_ACCESS",
        "source": "AIBOX_MOCK",
        "plate_number": plate_number,
        "plate_confidence": None,
        "camera_id": None,
        "camera_name": None,
        "device_serial_number": None,
        "plate_image_url": absolute_url(request, to_static_url(plate_path)),
        "frame_image_url": absolute_url(request, to_static_url(frame_path)),
        "driver_face_image_url": driver_face_url,
        "video_url": None,
        "detected_at": current_time,
        "created_at": current_time,
        "updated_at": current_time,
    }

    try:
        insert_row(db, "vehicle_access_logs", vehicle_data)
        update_by_key(
            db,
            "access_sessions",
            "session_id",
            session["session_id"],
            {
                "linked_vehicle_event_uid": vehicle_event_uid,
                "session_type": "VEHICLE_WITH_PERSON",
                "link_policy": "ALLOW_VEHICLE_LINK",
                "expected_plate_number": expected_plate or plate_number,
                "status": status_after_link,
                "updated_at": current_time,
            },
        )
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Lỗi trùng dữ liệu khi ghép xe: {exc}")

    updated_session = get_session_by_id(db, session["session_id"])
    return {
        "status": "SUCCESS",
        "message": "Đã ghép event xe vào session người đang chờ xe và lưu DB.",
        "data": {
            "person_event_uid": person_event_uid,
            "vehicle_event_uid": vehicle_event_uid,
            "session": updated_session,
            "vehicle": get_vehicle_log(db, vehicle_event_uid),
            "person": get_person_log(db, person_event_uid),
        },
    }

