from __future__ import annotations

import os
import uuid
from typing import Optional, Any

from app.core.config import settings
from app.services.face_service import FaceCandidateResult, select_human_face_candidates


def _save_crop_image(image_crop: Any, folder: str) -> Optional[str]:
    if image_crop is None:
        return None
    os.makedirs(folder, exist_ok=True)
    file_name = f"{uuid.uuid4().hex}.jpg"
    file_path = os.path.join(folder, file_name)
    image_crop.save(file_path)
    return f"/{file_path.replace(os.sep, '/')}"


def _normalize_image_crops(raw_crops: Any) -> list[Any]:
    if raw_crops is None:
        return []
    if isinstance(raw_crops, list):
        return raw_crops
    return [raw_crops]


def _filter_face_crops(image_crops: list[Any]) -> tuple[list[FaceCandidateResult], str, Optional[str]]:
    """Chỉ giữ crop có mặt người, bỏ logo/họa tiết/QR.

    Trả về:
    - face_candidates: danh sách crop đã xác nhận có mặt người, mặc định chỉ crop tốt nhất.
    - status: trạng thái lọc.
    - message: lỗi/cảnh báo nếu có.
    """
    if not image_crops:
        return [], "NO_IMAGE_CROPS", "Layout OCR không trả crop label=image nào."

    try:
        face_candidates = select_human_face_candidates(
            image_crops,
            min_det_score=0.25,
            min_face_area_ratio=0.01,
            keep_all=False,  # Chỉ lưu ảnh mặt tốt nhất, không lưu họa tiết/crop thừa.
        )
    except Exception as exc:
        # Không fail toàn bộ OCR. Text vẫn trả về, chỉ không lưu ảnh mặt.
        return [], "FACE_FILTER_ERROR", str(exc)

    if not face_candidates:
        return [], "NO_FACE_FOUND", "Có crop label=image nhưng không crop nào chứa mặt người. Đã bỏ các crop họa tiết/logo/QR."

    return face_candidates, "FACE_FOUND", None


def extract_cccd(image_path: str) -> dict:
    from gfl_core.app.ocr_service import extract_cccd as gfl_extract_cccd

    result = gfl_extract_cccd(image_path)
    image_crops = _normalize_image_crops(result.get("image"))

    face_candidates, face_filter_status, face_filter_message = _filter_face_crops(image_crops)

    candidate_urls: list[str] = []
    candidate_meta: list[dict] = []
    for candidate in face_candidates:
        url = _save_crop_image(candidate.image, settings.cccd_face_folder)
        if url:
            candidate_urls.append(url)
            candidate_meta.append(
                {
                    "source_crop_index": candidate.index,
                    "bbox": candidate.bbox,
                    "det_score": candidate.det_score,
                    "face_area_ratio": candidate.face_area_ratio,
                    "rank_score": candidate.rank_score,
                    "url": url,
                }
            )

    return {
        "id": result.get("id"),
        "name": result.get("name"),
        "birth": result.get("birth"),
        "sex": result.get("sex"),
        "place": result.get("place"),
        "cccd_face_image_url": candidate_urls[0] if candidate_urls else None,
        "cccd_face_candidate_urls": candidate_urls,
        "cccd_face_candidates": candidate_meta,
        "cccd_face_filter_status": face_filter_status,
        "cccd_face_filter_message": face_filter_message,
        "layout_image_crop_count": len(image_crops),
        "saved_face_crop_count": len(candidate_urls),
    }
