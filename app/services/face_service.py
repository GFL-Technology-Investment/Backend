from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

import cv2
import numpy as np
from PIL import Image


_FACE_MODEL: Any = None
_FACE_MODEL_ERROR: Optional[str] = None


@dataclass
class FaceEmbeddingResult:
    embedding: np.ndarray
    bbox: list[float]
    det_score: Optional[float]


@dataclass
class FaceCandidateResult:
    image: Any
    index: int
    bbox: list[float]
    det_score: Optional[float]
    face_area_ratio: float
    rank_score: float


def detect_best_face(image: Any) -> Optional[FaceEmbeddingResult]:
    return _get_best_face_embedding(image)


def select_human_face_candidates(
    images: Iterable[Any],
    *,
    min_det_score: float = 0.25,
    min_face_area_ratio: float = 0.01,
    keep_all: bool = False,
) -> list[FaceCandidateResult]:
    model = _load_face_model()
    candidates: list[FaceCandidateResult] = []

    for idx, image in enumerate(images):
        bgr = _to_bgr(image)
        if bgr is None:
            continue

        height, width = bgr.shape[:2]
        image_area = max(float(width * height), 1.0)
        faces = model.get(bgr)
        if not faces:
            continue

        best = max(
            faces,
            key=lambda f: float((f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])),
        )
        x1, y1, x2, y2 = [float(x) for x in best.bbox]
        face_area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        face_area_ratio = face_area / image_area
        det_score = float(getattr(best, "det_score", 0.0)) if hasattr(best, "det_score") else None

        if det_score is not None and det_score < min_det_score:
            continue
        if face_area_ratio < min_face_area_ratio:
            continue

        rank_score = face_area_ratio * (det_score if det_score is not None else 1.0)
        candidates.append(
            FaceCandidateResult(
                image=image,
                index=idx,
                bbox=[x1, y1, x2, y2],
                det_score=det_score,
                face_area_ratio=face_area_ratio,
                rank_score=rank_score,
            )
        )

    candidates.sort(key=lambda item: item.rank_score, reverse=True)
    if keep_all:
        return candidates
    return candidates[:1]


def _load_face_model() -> Any:
    """Lazy-load InsightFace model.

    Nếu insightface chưa được cài, raise RuntimeError có message rõ để người chạy biết
    cần cài `insightface`.
    """
    global _FACE_MODEL, _FACE_MODEL_ERROR

    if _FACE_MODEL is not None:
        return _FACE_MODEL

    if _FACE_MODEL_ERROR:
        raise RuntimeError(_FACE_MODEL_ERROR)

    try:
        from insightface.app import FaceAnalysis

        model = FaceAnalysis(name="buffalo_sc", providers=["CPUExecutionProvider"])
        model.prepare(ctx_id=-1, det_size=(320, 320))
        _FACE_MODEL = model
        return model
    except Exception as exc:  # pragma: no cover - phụ thuộc môi trường local
        _FACE_MODEL_ERROR = (
            "Không khởi tạo được InsightFace. Hãy cài dependency: "
            "pip install insightface onnxruntime opencv-python. "
            f"Chi tiết lỗi: {exc}"
        )
        raise RuntimeError(_FACE_MODEL_ERROR) from exc


def get_face_model_status() -> dict:
    """Trả trạng thái model để debug nhanh."""
    if _FACE_MODEL is not None:
        return {"available": True, "model": "InsightFace buffalo_sc", "error": None}
    if _FACE_MODEL_ERROR:
        return {"available": False, "model": "InsightFace buffalo_sc", "error": _FACE_MODEL_ERROR}
    return {"available": None, "model": "InsightFace buffalo_sc", "error": "Chưa lazy-load model"}


def _to_bgr(image: Any) -> Optional[np.ndarray]:
    if image is None:
        return None
    if isinstance(image, np.ndarray):
        if image.ndim == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        return image
    if isinstance(image, Image.Image):
        return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    raise TypeError(f"Không hỗ trợ image type: {type(image)!r}")


def _read_image(image_path: str) -> Image.Image:
    return Image.open(image_path).convert("RGB")


def _get_best_face_embedding(image: Any) -> Optional[FaceEmbeddingResult]:
    model = _load_face_model()
    bgr = _to_bgr(image)
    if bgr is None:
        return None

    faces = model.get(bgr)
    if not faces:
        return None

    best = max(
        faces,
        key=lambda f: float((f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])),
    )
    return FaceEmbeddingResult(
        embedding=best.normed_embedding,
        bbox=[float(x) for x in best.bbox],
        det_score=float(getattr(best, "det_score", 0.0)) if hasattr(best, "det_score") else None,
    )


def face_recognition(images: list, image_cam: Any) -> dict:
    cam_result = _get_best_face_embedding(image_cam)
    if cam_result is None:
        raise ValueError("Không tìm thấy khuôn mặt trong ảnh camera/bảo vệ")

    embeddings: list[np.ndarray] = []
    valid_indices: list[int] = []
    bboxes: list[list[float]] = []
    det_scores: list[Optional[float]] = []

    for idx, img in enumerate(images):
        emb_result = _get_best_face_embedding(img)
        if emb_result is not None:
            embeddings.append(emb_result.embedding)
            valid_indices.append(idx)
            bboxes.append(emb_result.bbox)
            det_scores.append(emb_result.det_score)

    if not embeddings:
        return {
            "matched_image": None,
            "similarity": -1.0,
            "index": None,
            "message": "Không tìm thấy khuôn mặt trong ảnh CCCD",
        }

    scores = np.stack(embeddings) @ cam_result.embedding
    best_pos = int(np.argmax(scores))
    best_idx = valid_indices[best_pos]

    return {
        "matched_image": images[best_idx],
        "similarity": float(scores[best_pos]),
        "index": best_idx,
        "cccd_face_bbox": bboxes[best_pos],
        "cccd_face_det_score": det_scores[best_pos],
        "live_face_bbox": cam_result.bbox,
        "live_face_det_score": cam_result.det_score,
        "message": "OK",
    }


def compare_face_image_paths(cccd_image_paths: Iterable[str], live_face_image_path: str) -> dict:
    candidate_paths = [str(path) for path in cccd_image_paths if path]
    if not candidate_paths:
        raise ValueError("Không có ảnh CCCD/ảnh mặt CCCD để so sánh")
    if not live_face_image_path:
        raise ValueError("Không có ảnh live/camera để so sánh")

    cccd_images = [_read_image(path) for path in candidate_paths]
    live_image = _read_image(live_face_image_path)

    result = face_recognition(cccd_images, live_image)
    matched_index = result.get("index")
    result.pop("matched_image", None)  # Không trả object ảnh qua API/JSON.
    result["cccd_image_path"] = candidate_paths[matched_index] if matched_index is not None else None
    result["live_face_image_path"] = live_face_image_path
    return result
