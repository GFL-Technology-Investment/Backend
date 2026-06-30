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

@router.post("/api/v1/face/compare")
async def real_face_compare(
    request: Request,
    event_uid: str = Form(..., description="event_uid LPR-... hoặc PERSON-..."),
    live_face_image: Optional[UploadFile] = File(None, description="Ảnh mặt thật từ camera/bảo vệ. Nếu bỏ trống sẽ dùng live_face_image_url đã có trong DB."),
    cccd_face_image: Optional[UploadFile] = File(None, description="Ảnh mặt CCCD tùy chọn. Nếu bỏ trống sẽ dùng cccd_face_image_url/cccd_original_image_url trong DB."),
    threshold: float = Form(0.45, description="Ngưỡng InsightFace cosine similarity. Có thể hiệu chỉnh theo dữ liệu thực tế."),
    source: str = Form("CAMERA", description="CAMERA hoặc GUARD_CAPTURE"),
    issue_ticket: bool = Form(False, description="True để tự tạo vé ngay khi MATCH -> CHECKED_IN"),
    db: sqlite3.Connection = Depends(get_db),
):
    """So sánh mặt thật bằng InsightFace.

    Luồng dùng:
    - Luồng A: xe đến trước, OCR xong đã có driver_face_image_url từ camera mock/thật.
      Gọi endpoint này với event_uid=LPR-...
    - Luồng B: người đến trước, xe đến sau. Sau khi ghép xe có driver_face_image_url.
      Gọi endpoint này với event_uid=PERSON-... hoặc LPR-...
    - Luồng C: PERSON_ONLY. FE/bảo vệ upload live_face_image trực tiếp.
      Gọi endpoint này với event_uid=PERSON-...
    """
    person_log = get_person_log(db, event_uid)

    # Nếu event_uid là LPR hoặc event chính của session, tìm person_log bằng session_id.
    if not person_log:
        session = find_session_by_event_uid(db, event_uid)
        if session:
            person_log = get_person_log_by_session_id(db, session.get("session_id"))

    if not person_log:
        raise HTTPException(status_code=404, detail="Chưa có person log để so sánh mặt")

    session = get_session_by_id(db, person_log["session_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Không tìm thấy session")

    if is_session_closed_or_final(session):
        raise HTTPException(status_code=400, detail="Session đã kết thúc hoặc bị từ chối/hết hạn, không thể so sánh mặt.")

    if session.get("status") == "CHECKED_IN" and person_log.get("face_compare_result") == "MATCH":
        return {
            "status": "DUPLICATE_FACE_COMPARE",
            "message": "Session đã CHECKED_IN và face_compare_result = MATCH. Không chạy model lại.",
            "data": {
                "compare": {
                    "score": person_log.get("face_compare_score"),
                    "threshold": person_log.get("face_compare_threshold"),
                    "result": person_log.get("face_compare_result"),
                },
                "session": session,
                "person": person_log,
                "ticket": get_latest_ticket_by_session_id(db, session["session_id"]),
            },
        }

    current_time = now_vn()

    # 1) Xác định ảnh CCCD candidates.
    cccd_candidate_paths: List[str] = []
    cccd_face_url_to_save = None

    if cccd_face_image:
        cccd_face_path = save_upload_file(cccd_face_image, MEDIA_FOLDER, f"{person_log['event_uid']}-cccd-face-upload")
        cccd_candidate_paths.append(cccd_face_path)
        cccd_face_url_to_save = absolute_url(request, to_static_url(cccd_face_path))
    else:
        for key in ["cccd_face_image_url", "cccd_original_image_url"]:
            local_path = static_url_to_local_path(person_log.get(key))
            if local_path and os.path.exists(local_path):
                cccd_candidate_paths.append(local_path)

    if not cccd_candidate_paths:
        raise HTTPException(status_code=422, detail="Không tìm thấy file ảnh CCCD/ảnh mặt CCCD để so sánh")

    # 2) Xác định ảnh live/camera.
    live_face_url_to_save = None
    if live_face_image:
        live_face_path = save_upload_file(live_face_image, MEDIA_FOLDER, f"{person_log['event_uid']}-live-face")
        live_face_url_to_save = absolute_url(request, to_static_url(live_face_path))
    else:
        live_face_path = static_url_to_local_path(person_log.get("live_face_image_url"))
        if not live_face_path:
            vehicle_log = get_vehicle_log_by_session_id(db, session.get("session_id"))
            live_face_path = static_url_to_local_path((vehicle_log or {}).get("driver_face_image_url"))
            live_face_url_to_save = (vehicle_log or {}).get("driver_face_image_url")

    if not live_face_path or not os.path.exists(live_face_path):
        raise HTTPException(
            status_code=422,
            detail="Không tìm thấy ảnh mặt thật từ camera/bảo vệ. Hãy upload live_face_image hoặc đảm bảo session đã có driver_face_image_url.",
        )

    # 3) Chạy InsightFace trong executor để không block event loop.
    try:
        compare_payload = await asyncio.get_running_loop().run_in_executor(
            None,
            compare_face_image_paths,
            cccd_candidate_paths,
            live_face_path,
        )
        score = float(compare_payload.get("similarity", -1.0))
        final_result = "MATCH" if score >= threshold else "NO_MATCH"
        compare_message = compare_payload.get("message") or "OK"
    except Exception as exc:
        # Không tìm thấy mặt hoặc model lỗi -> đưa vào NEED_REVIEW để bảo vệ xử lý.
        compare_payload = {
            "similarity": -1.0,
            "index": None,
            "cccd_image_path": None,
            "live_face_image_path": live_face_path,
            "message": str(exc),
        }
        score = -1.0
        final_result = "NEED_REVIEW"
        compare_message = str(exc)

    # 4) Cập nhật person log.
    person_update = {
        "face_compare_source": source,
        "face_compare_score": score,
        "face_compare_threshold": threshold,
        "face_compare_result": final_result,
        "updated_at": current_time,
    }
    if cccd_face_url_to_save:
        person_update["cccd_face_image_url"] = cccd_face_url_to_save
    if live_face_url_to_save:
        person_update["live_face_image_url"] = live_face_url_to_save
        person_update["live_face_source"] = source

    update_by_key(db, "person_access_logs", "event_uid", person_log["event_uid"], person_update)

    # 5) Cập nhật session status.
    session_status = "CHECKED_IN" if final_result == "MATCH" else "NEED_REVIEW"
    session_update_data = {
        "status": session_status,
        "updated_at": current_time,
    }
    if session_status == "CHECKED_IN":
        session_update_data["checked_in_at"] = current_time

    # PERSON_ONLY sau face compare luôn khóa để tránh ghép nhầm xe về sau.
    if session.get("session_type") == "PERSON_ONLY":
        session_update_data["link_policy"] = "PERSON_ONLY_LOCKED"

    update_by_key(db, "access_sessions", "session_id", session["session_id"], session_update_data)
    db.commit()

    updated_session = get_session_by_id(db, session["session_id"])
    ticket = None
    if updated_session and updated_session.get("status") == "CHECKED_IN":
        ticket = issue_ticket_for_session(
            db=db,
            request=request,
            session=updated_session,
            ticket_type="VEHICLE_PASS" if updated_session.get("session_type") == "VEHICLE_WITH_PERSON" else "PERSON_ONLY_PASS",
            issued_by="FACE_COMPARE_SERVICE",
            force_reissue=False,
        )

    return {
        "status": "SUCCESS",
        "data": {
            "compare": {
                "model": "InsightFace buffalo_sc",
                "score": score,
                "threshold": threshold,
                "result": final_result,
                "message": compare_message,
                "raw": compare_payload,
            },
            "session": updated_session,
            "person": get_person_log(db, person_log["event_uid"]),
            "ticket": ticket,
        },
    }


