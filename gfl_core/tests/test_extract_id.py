import cv2

from gfl_core.app.ocr_service import extract_cccd
from gfl_core.app.verify_face import face_recognition

if __name__ == "__main__":
    result = extract_cccd("./gfl_core/tests/data/id.png")
    print(result)

    if len(result["image"]) > 1:
        face_result = face_recognition(
            images=result["image"],
            image_cam=cv2.imread("./gfl_core/tests/data/face.jpg"),
        )
        print(f"similarity={face_result['similarity']:.4f}, index={face_result['index']}")