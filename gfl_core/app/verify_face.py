import cv2
import numpy as np

from insightface.app import FaceAnalysis

face_model = FaceAnalysis(name="buffalo_sc", providers=["CPUExecutionProvider"])
face_model.prepare(ctx_id=-1, det_size=(320, 320))



def _to_bgr(image) -> np.ndarray | None:
    if image is None:
        return None
    if isinstance(image, np.ndarray):
        return image
    return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)


def _get_best_face_embedding(image) -> np.ndarray | None:
    bgr = _to_bgr(image)
    if bgr is None:
        return None
    faces = face_model.get(bgr)
    if not faces:
        return None
    best = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    return best.normed_embedding


def face_recognition(images: list, image_cam) -> dict:
    cam_emb = _get_best_face_embedding(image_cam)
    if cam_emb is None:
        raise ValueError("Không tìm thấy khuôn mặt trong ảnh camera")

    embeddings, valid_indices = [], []
    for idx, img in enumerate(images):
        emb = _get_best_face_embedding(img)
        if emb is not None:
            embeddings.append(emb)
            valid_indices.append(idx)

    if not embeddings:
        return {"matched_image": None, "similarity": -1.0, "index": None}

    scores = np.stack(embeddings) @ cam_emb
    best_pos = int(np.argmax(scores))
    best_idx = valid_indices[best_pos]

    return {
        "matched_image": images[best_idx],
        "similarity": float(scores[best_pos]),
        "index": best_idx,
    }