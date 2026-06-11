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

@router.post("/ocr/cccd")
async def ocr_cccd(
    request: Request,
    image: UploadFile = File(..., description="Ảnh CCCD"),
    event_uid: Optional[str] = Form(None, description="event_uid từ /mock/aibox/lpr-event nếu muốn ghép với xe"),
    expected_plate_number: Optional[str] = Form(None, description="Biển số dự kiến nếu người OCR trước rồi chờ xe"),
    organization_id: str = Form("org-001"),
    location_id: str = Form("loc-001"),
    gate_id: str = Form("gate-001"),
    gate_name: str = Form("Cổng vào 01"),
    db: sqlite3.Connection = Depends(get_db),
):
    # Lưu ảnh CCCD gốc bằng tên random, tránh trùng file hoặc ghi đè file cũ.
    original_path = save_upload_file(image, CCCD_ORIGINAL_FOLDER, "cccd-original")
    cccd_image_hash = get_file_sha256(original_path)

    # Nếu FE bấm gửi lại cùng ảnh khi chưa có event xe thì trả lại session active cũ, không OCR lại.
    # Với luồng A có event_uid, vẫn cần kiểm tra session theo event_uid ở phía dưới.
    if not event_uid:
        duplicated_person_session = find_active_person_session_by_image_hash(db, cccd_image_hash)
        if duplicated_person_session:
            return duplicate_response(
                "DUPLICATE_CCCD_IMAGE",
                duplicated_person_session,
                db,
                "Ảnh CCCD này đã được xử lý trong một session active. Không OCR/tạo session mới.",
            )

    # Gọi OCR model trong thread pool để không block event loop của FastAPI.
    result = await asyncio.get_running_loop().run_in_executor(None, extract_cccd, original_path)
    validate_cccd_ocr_result(result)

    if result.get("cccd_face_image_url"):
        result["cccd_face_image_url"] = absolute_url(request, result["cccd_face_image_url"])

    if result.get("cccd_face_candidate_urls"):
        result["cccd_face_candidate_urls"] = [
            absolute_url(request, url) for url in result["cccd_face_candidate_urls"]
        ]

    if result.get("cccd_face_candidates"):
        for candidate in result["cccd_face_candidates"]:
            if candidate.get("url"):
                candidate["url"] = absolute_url(request, candidate["url"])

    result["cccd_original_image_url"] = absolute_url(request, to_static_url(original_path))

    linked_session = None
    current_time = now_vn()
    expected_plate_number = normalize_plate_number(expected_plate_number)

    # Luồng A: xe đến trước, OCR người sau. Có event_uid để ghép vào session WAITING_PERSON.
    if event_uid:
        vehicle_log = get_vehicle_log(db, event_uid)
        if not vehicle_log:
            raise HTTPException(status_code=404, detail="event_uid không tồn tại trong vehicle_access_logs")

        session = get_session_by_id(db, vehicle_log["session_id"])
        if not session:
            raise HTTPException(status_code=404, detail="Không tìm thấy session tương ứng event_uid")

        assert_can_link_person_to_vehicle(session)

        existing_person = get_person_log_by_session_id(db, session["session_id"])
        if existing_person:
            return {
                "status": "DUPLICATE_PERSON_ALREADY_LINKED",
                "message": "Session xe này đã có người được ghép. Backend trả lại dữ liệu cũ, không tạo thêm person log.",
                "data": build_session_detail(db, event_uid),
            }

        person_data = {
            "event_uid": event_uid,
            "session_id": session["session_id"],
            "event_type": "OCR_CCCD",
            "source": "OCR_SERVICE",
            "cccd_number": result.get("id"),
            "full_name": result.get("name"),
            "birth": result.get("birth"),
            "sex": result.get("sex"),
            "place": result.get("place"),
            "cccd_face_image_url": result.get("cccd_face_image_url"),
            "cccd_original_image_url": result.get("cccd_original_image_url"),
            "cccd_image_hash": cccd_image_hash,
            "live_face_image_url": vehicle_log.get("driver_face_image_url"),
            "live_face_source": "CAMERA" if vehicle_log.get("driver_face_image_url") else None,
            "face_compare_source": None,
            "face_compare_score": None,
            "face_compare_threshold": None,
            "face_compare_result": "PENDING",
            "created_at": current_time,
            "updated_at": current_time,
        }

        upsert_row(db, "person_access_logs", person_data)
        update_by_key(
            db,
            "access_sessions",
            "session_id",
            session["session_id"],
            {
                "status": "WAITING_FACE_COMPARE",
                "cccd_number": result.get("id"),
                "full_name": result.get("name"),
                "updated_at": current_time,
            },
        )
        db.commit()
        linked_session = get_session_by_id(db, session["session_id"])

    # Luồng B/C: người OCR trước, chưa có event xe. Tạo session WAITING_VEHICLE hoặc PERSON_ONLY.
    else:
        person_event_uid = f"PERSON-{uuid.uuid4().hex[:12]}"
        session_id = f"SESSION-{uuid.uuid4().hex[:12]}"
        session_type = "VEHICLE_WITH_PERSON" if expected_plate_number else "PERSON_ONLY"
        session_status = "WAITING_VEHICLE" if expected_plate_number else "WAITING_FACE_COMPARE"

        session_data = {
            "session_id": session_id,
            "session_code": session_id,
            "event_uid": person_event_uid,
            "linked_vehicle_event_uid": None,
            "session_type": session_type,
            "organization_id": organization_id,
            "location_id": location_id,
            "gate_id": gate_id,
            "gate_name": gate_name,
            "status": session_status,
            "link_policy": "ALLOW_VEHICLE_LINK",
            "expected_plate_number": expected_plate_number,
            "cccd_number": result.get("id"),
            "full_name": result.get("name"),
            "checked_in_at": None,
            "checked_out_at": None,
            "created_at": current_time,
            "updated_at": current_time,
        }

        person_data = {
            "event_uid": person_event_uid,
            "session_id": session_id,
            "event_type": "OCR_CCCD",
            "source": "OCR_SERVICE",
            "cccd_number": result.get("id"),
            "full_name": result.get("name"),
            "birth": result.get("birth"),
            "sex": result.get("sex"),
            "place": result.get("place"),
            "cccd_face_image_url": result.get("cccd_face_image_url"),
            "cccd_original_image_url": result.get("cccd_original_image_url"),
            "cccd_image_hash": cccd_image_hash,
            "live_face_image_url": None,
            "live_face_source": None,
            "face_compare_source": None,
            "face_compare_score": None,
            "face_compare_threshold": None,
            "face_compare_result": "PENDING",
            "created_at": current_time,
            "updated_at": current_time,
        }

        try:
            insert_row(db, "access_sessions", session_data)
            insert_row(db, "person_access_logs", person_data)
            db.commit()
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail=f"Lỗi trùng dữ liệu khi tạo person session: {exc}")

        linked_session = get_session_by_id(db, session_id)
        event_uid = person_event_uid

    return {
        "status": "SUCCESS",
        "data": result,
        "linked_session": linked_session,
        "event_uid": event_uid,
    }

