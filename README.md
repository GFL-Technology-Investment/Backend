# OCR CCCD + Access Control API — `gfl_core` + FastAPI

Project này là backend PoC để test luồng kiểm soát ra/vào:

- OCR CCCD bằng `gfl_core`
- So sánh mặt thật bằng InsightFace
- Mock AIBox/Camera để test người ↔ xe
- Lưu phiên ra/vào vào SQLite
- API lịch sử ra/vào cho FE
- Tạo vé, QR code thật, barcode thật
- Checkout bằng `event_uid` hoặc `ticket_code`

Project đã thống nhất quản lý dependency bằng:

```txt
pyproject.toml
uv.lock
```

Không dùng `requirements.txt` nữa.

---

## 1. Cấu trúc project

```txt
app/
  main.py                         # tạo FastAPI app, mount static, CORS, register router

  api/
    router.py                     # gom các router
    routes/
      health.py                   # GET /
      ocr.py                      # POST /ocr/cccd
      aibox_mock.py               # mock camera/AIBox
      face.py                     # face compare thật + mock face compare
      access.py                   # session listing + checkout
      tickets.py                  # issue/print/verify/checkout ticket
      history.py                  # history APIs cho FE

  core/
    config.py                     # path, setting, runtime folders
    time.py                       # giờ Việt Nam
    status.py                     # enum/status thống nhất
    files.py                      # đổi static URL <-> local path

  services/
    access_service.py             # rule link người-xe, DB helper, build response
    ocr_service.py                # adapter gọi gfl_core OCR và trả URL ảnh
    face_service.py               # InsightFace service
    ticket_renderer.py            # render vé + QR thật + barcode thật

  database.py                     # SQLite schema + migration nhẹ

gfl_core/                         # core OCR/model engine
  app/
  models/
  utils/

static/                           # ảnh runtime: CCCD, media, tickets
uploads/                          # upload temp/test
pyproject.toml                    # dependency khai báo ở đây
uv.lock                           # khóa version dependency
```

---

## 2. Yêu cầu môi trường

Khuyến nghị:

```txt
Python 3.10 hoặc 3.11
uv
```

> Dự án có `torch`, `insightface`, `onnxruntime`, `opencv-python`, `vietocr`, `transformers`, nên lần đầu cài có thể lâu.

---

## 3. Cài `uv`

### Windows PowerShell

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Đóng terminal rồi mở lại, kiểm tra:

```bash
uv --version
```

### macOS/Linux

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```

---

## 4. Cài dependency bằng `uv`

Đứng tại thư mục gốc project, nơi có `pyproject.toml` và `uv.lock`:

```bash
uv sync
```

Lệnh này sẽ:

```txt
1. Tạo virtual environment .venv nếu chưa có
2. Đọc pyproject.toml
3. Cài đúng version đã khóa trong uv.lock
```

Nếu muốn ép dùng Python 3.10:

```bash
uv python install 3.10
uv sync --python 3.10
```

---

## 5. Chạy backend

Cách khuyến nghị:

```bash
uv run uvicorn app.main:app --reload
```

Swagger:

```txt
http://127.0.0.1:8000/docs
```

Health check:

```txt
http://127.0.0.1:8000/
```

---

## 6. Lưu ý model/cache

Project không đặt weight trực tiếp trong source.

Các model có thể được tải/cache bởi:

```txt
HuggingFace / Transformers  → PPDocLayoutV3
VietOCR                     → vgg_transformer
InsightFace                 → buffalo_sc
```

Lần đầu chạy OCR hoặc face compare thật có thể mất thời gian tải model.

Nếu máy không có internet, cần chuẩn bị cache model trước.

---

## 7. Luồng test chính

### Luồng A — Xe đến trước, người OCR sau

1. Gọi:

```txt
POST /mock/aibox/lpr-event
```

Nhập:

```txt
plate_number
plate_image
frame_image
driver_face_image nếu có
```

Copy `event_uid` dạng `LPR-...`.

2. Gọi:

```txt
POST /ocr/cccd
```

Upload ảnh CCCD và nhập `event_uid` LPR ở bước 1.

3. Face compare:

```txt
POST /api/v1/face/compare
```

hoặc mock:

```txt
POST /mock/face/compare
```

Nếu `MATCH`, session chuyển thành:

```txt
CHECKED_IN
```

---

### Luồng B — Người đến trước, xe đến sau

1. Gọi:

```txt
POST /ocr/cccd
```

Upload CCCD và nhập:

```txt
expected_plate_number
```

Copy `event_uid` dạng `PERSON-...`.

2. Gọi:

```txt
POST /mock/aibox/link-vehicle-to-person
```

Nhập:

```txt
person_event_uid
plate_number
plate_image
frame_image
driver_face_image nếu có
```

3. Face compare:

```txt
POST /api/v1/face/compare
```

hoặc:

```txt
POST /mock/face/compare
```

---

### Luồng C — PERSON_ONLY

1. Gọi:

```txt
POST /ocr/cccd
```

Chỉ upload CCCD, không nhập `event_uid`, không nhập `expected_plate_number`.

2. Gọi face compare:

```txt
POST /api/v1/face/compare
```

hoặc:

```txt
POST /mock/face/compare
```

Nếu `MATCH`:

```txt
status = CHECKED_IN
link_policy = PERSON_ONLY_LOCKED
```

Phiên này không cho ghép xe về sau.

---

## 8. Vé / QR thật

Tạo vé:

```txt
POST /api/v1/tickets/issue
```

Hoặc trong face compare có thể bật:

```txt
issue_ticket = true
```

Backend render:

```txt
front_image_url
back_image_url
qr_image_url
barcode_image_url
ticket_code
```

QR encode URL verify:

```txt
GET /api/v1/tickets/verify/{ticket_code}
```

Checkout bằng vé:

```txt
POST /api/v1/tickets/checkout
```

Checkout chung:

```txt
POST /api/v1/access/checkout
```

---

## 9. API cho FE

```txt
POST /ocr/cccd
POST /mock/aibox/lpr-event
POST /mock/aibox/link-vehicle-to-person
POST /api/v1/face/compare
POST /mock/face/compare
GET  /api/v1/access/history
GET  /api/v1/access/history/{event_uid}
POST /api/v1/access/checkout
POST /api/v1/tickets/issue
GET  /api/v1/tickets/{ticket_id}
GET  /api/v1/tickets/verify/{ticket_code}
POST /api/v1/tickets/checkout
```

---

## 10. SQLite DB

SQLite mặc định:

```txt
access_control.db
```

Đổi path DB:

```bash
ACCESS_DB_PATH=data/access_control.db uv run uvicorn app.main:app --reload
```

Bảng chính:

```txt
access_sessions
vehicle_access_logs
person_access_logs
tickets
ticket_print_logs
```

---

## 11. Status chuẩn

Session status:

```txt
WAITING_PERSON
WAITING_VEHICLE
WAITING_FACE_COMPARE
CHECKED_IN
CHECKED_OUT
NEED_REVIEW
REJECTED
EXPIRED
```

Face compare result:

```txt
PENDING
MATCH
NO_MATCH
NEED_REVIEW
```

Link policy:

```txt
ALLOW_VEHICLE_LINK
PERSON_ONLY_LOCKED
```

---

## 12. Lệnh kiểm tra nhanh

Compile kiểm tra syntax:

```bash
uv run python -m py_compile app/main.py app/database.py
```

Chạy server:

```bash
uv run uvicorn app.main:app --reload
```

Mở Swagger:

```txt
http://127.0.0.1:8000/docs
```

---

## 13. Ràng buộc chống trùng / chống spam request

Bản này đã thêm các ràng buộc để tránh camera/FE gửi trùng làm tạo nhiều session và làm chậm hệ thống.

### 13.1. Chống trùng event xe

Khi gọi:

```txt
POST /mock/aibox/lpr-event
```

Backend kiểm tra theo thứ tự:

```txt
1. event_uid đã tồn tại chưa
   → Nếu có: trả DUPLICATE_EVENT_UID, không insert thêm.

2. Có session WAITING_VEHICLE khớp expected_plate_number không
   → Nếu có: tự ghép xe vào session người đang chờ xe.

3. Biển số đang có active session cùng organization/gate không
   → Nếu có: trả DUPLICATE_ACTIVE_VEHICLE_SESSION, không tạo session mới.

4. Camera vừa gửi cùng biển số trong khoảng rất gần không
   → Nếu có: trả DUPLICATE_RECENT_DETECTION.

5. Không trùng
   → Tạo session VEHICLE_WITH_PERSON / WAITING_PERSON.
```

Các status được xem là active:

```txt
WAITING_PERSON
WAITING_VEHICLE
WAITING_FACE_COMPARE
CHECKED_IN
NEED_REVIEW
```

Các status được xem là đã kết thúc:

```txt
CHECKED_OUT
REJECTED
EXPIRED
```

### 13.2. Chống OCR ảnh CCCD trùng

Khi gọi:

```txt
POST /ocr/cccd
```

Backend tính SHA256 của ảnh CCCD gốc và lưu vào:

```txt
person_access_logs.cccd_image_hash
```

Nếu cùng ảnh CCCD đã tồn tại trong một session active, backend trả:

```txt
DUPLICATE_CCCD_IMAGE
```

và không chạy OCR/tạo session mới.

### 13.3. Chống face compare chạy lại

Nếu session đã:

```txt
status = CHECKED_IN
face_compare_result = MATCH
```

mà gọi lại:

```txt
POST /api/v1/face/compare
POST /mock/face/compare
```

backend trả:

```txt
DUPLICATE_FACE_COMPARE
```

và không chạy model/cập nhật lại.

### 13.4. Checkout idempotent

Nếu session đã CHECKED_OUT mà gọi checkout lại, backend trả:

```txt
ALREADY_CHECKED_OUT
```

và không cập nhật lại dữ liệu.

### 13.5. DB constraint / index đã thêm

Các bảng đã bổ sung:

```txt
CHECK constraint cho session_type, status, link_policy
CHECK constraint cho face_compare_result
CHECK constraint cho ticket status và print status
UNIQUE event_uid cho vehicle/person logs
Index cho plate_number, camera_id/detected_at, cccd_image_hash, status, checked_in_at, checked_out_at
```

Các ràng buộc này giúp backend không tạo dữ liệu trùng khi camera retry, FE bấm nhiều lần hoặc OCR/face compare bị gọi lặp.

---

## Lọc ảnh mặt CCCD, bỏ ảnh họa tiết/logo/QR

Từ bản này, OCR service không lưu toàn bộ crop `label=image` nữa.
Layout model có thể trả nhiều vùng ảnh trên CCCD, ví dụ ảnh chân dung, quốc huy, QR code hoặc họa tiết nền. Backend sẽ dùng InsightFace để kiểm tra từng crop và chỉ lưu crop có khuôn mặt người vào thư mục:

```txt
static/cccd_faces/
```

Response `/ocr/cccd` có thêm các trường debug:

```json
{
  "cccd_face_image_url": "http://127.0.0.1:8000/static/cccd_faces/xxx.jpg",
  "cccd_face_filter_status": "FACE_FOUND",
  "cccd_face_filter_message": null,
  "layout_image_crop_count": 3,
  "saved_face_crop_count": 1
}
```

Các trạng thái có thể có:

```txt
FACE_FOUND        Có tìm thấy và lưu ảnh mặt người.
NO_FACE_FOUND     Có crop image nhưng không crop nào chứa mặt người, đã bỏ họa tiết/logo/QR.
NO_IMAGE_CROPS    Layout OCR không trả vùng image nào.
FACE_FILTER_ERROR InsightFace/model lỗi, OCR text vẫn trả về nhưng không lưu ảnh mặt.
```

Nếu `cccd_face_image_url = null`, API face compare vẫn có thể fallback sang `cccd_original_image_url` để InsightFace tìm mặt trên ảnh CCCD gốc.
