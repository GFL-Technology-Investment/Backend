# OCR CCCD + Access Control API — `gfl_core` + FastAPI

Project này là backend PoC để test luồng kiểm soát ra/vào:

- OCR CCCD bằng `gfl_core`
- So sánh mặt thật bằng InsightFace
- Mock AIBox/Camera để test người ↔ xe
- Lưu phiên ra/vào vào SQLite
- API lịch sử ra/vào cho FE
- Tạo vé, QR code thật, barcode thật
- Checkout bằng `event_uid` hoặc `ticket_code`
- **Xác thực nội bộ (dev-login) và đăng nhập SSO qua OpenID Connect
  (Keycloak/Azure AD)** — xem Phần 7.

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
    deps/
      auth.py                     # require_internal_auth, require_permission, require_camera_auth
    routes/
      health.py                   # GET /
      auth.py                     # dev-login, /api/v1/auth/me, /api/v1/auth/azure/exchange (SSO)
      ocr.py                      # POST /ocr/cccd
      aibox_mock.py               # mock camera/AIBox
      face.py                     # face compare thật + mock face compare
      access.py                   # session listing + checkout
      tickets.py                  # issue/print/verify/checkout ticket
      history.py                  # history APIs cho FE

  core/
    config.py                     # path, setting, runtime folders, biến AUTH_*/AZURE_*
    security.py                   # create_internal_jwt(), verify JWT nội bộ
    oidc.py                       # verify id_token/access_token từ Keycloak/Azure AD (SSO)
    time.py                       # giờ Việt Nam
    status.py                     # enum/status thống nhất, mã lỗi auth
    files.py                      # đổi static URL <-> local path

  services/
    access_service.py             # rule link người-xe, DB helper, build response
    ocr_service.py                # adapter gọi gfl_core OCR và trả URL ảnh
    face_service.py                # InsightFace service
    ticket_renderer.py             # render vé + QR thật + barcode thật

  database.py                     # SQLite schema + migration nhẹ (bao gồm bảng users, organizations)

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

> **⚠️ Cần kiểm tra lại `pyproject.toml`:** phần xác thực SSO (`app/core/oidc.py`)
> dùng `PyJWT` và phần đọc `.env` (`app/core/config.py`) dùng `python-dotenv`,
> nhưng 2 package này **hiện chưa được khai báo** trong `dependencies` của
> `pyproject.toml` (được cài thủ công vào `.venv` lúc phát triển). Người khác
> `uv sync` lần đầu sẽ thiếu 2 package này. Cần bổ sung trước khi bàn giao:
>
> ```toml
> dependencies = [
>     ...
>     "pyjwt[crypto]>=2.8.0",
>     "python-dotenv>=1.0.0",
> ]
> ```
>
> rồi chạy `uv lock` để cập nhật `uv.lock`.

---

## 5. Cấu hình biến môi trường (`.env`)

File `.env` đặt tại thư mục gốc (cùng cấp `pyproject.toml`), được nạp tự động
qua `python-dotenv` (`load_dotenv()` gọi trong `app/core/config.py`).

```env
# Tổ chức/cổng mặc định
DEFAULT_ORGANIZATION_ID=org-001
DEFAULT_LOCATION_ID=loc-001
DEFAULT_GATE_ID=gate-001
DEFAULT_GATE_NAME=Cổng vào 01

# Auth nội bộ
AUTH_ENABLED=true
AUTH_DEV_MODE=true
INTERNAL_JWT_SECRET=doi-chuoi-nay-o-production
INTERNAL_JWT_ISSUER=gfl-core
INTERNAL_JWT_AUDIENCE=gfl-internal-api
INTERNAL_JWT_EXPIRE_SECONDS=28800

# Camera Auth
CAMERA_TOKEN_HASH_PEPPER=doi-chuoi-nay-o-production
DEV_CAMERA_TOKEN=dev-camera-token

# SSO / OIDC — xem Phần 7. Giá trị dưới đây dùng để test với Keycloak local.
AZURE_TENANT_ID=gfl-demo
AZURE_CLIENT_ID=gfl-backend-app2
AZURE_ISSUER=http://localhost:8081/realms/gfl-demo
AZURE_JWKS_URL=http://localhost:8081/realms/gfl-demo/protocol/openid-connect/certs
AZURE_ROLES_CLAIM=realm_access.roles
```

> **Production:** `INTERNAL_JWT_SECRET`, `CAMERA_TOKEN_HASH_PEPPER` bắt buộc
> phải đổi khỏi giá trị mặc định; `AUTH_DEV_MODE` bắt buộc `false` (khóa
> `/api/v1/auth/dev-login`).

---

## 6. Chạy backend

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

## 7. XÁC THỰC (AUTHENTICATION) & ĐĂNG NHẬP SSO

Hệ thống có **2 cơ chế xác thực song song** cho FE/người dùng nội bộ (khác
với xác thực Camera/AIBox dùng Bearer token riêng — xem `require_camera_auth`):

| Cơ chế                   | Endpoint                           | Dùng khi nào                                             |
| ------------------------ | ---------------------------------- | -------------------------------------------------------- |
| **Dev Login**            | `POST /api/v1/auth/dev-login`      | Test nhanh local, chỉ hoạt động khi `AUTH_DEV_MODE=true` |
| **SSO (OpenID Connect)** | `POST /api/v1/auth/azure/exchange` | Đăng nhập thật qua Keycloak (test)/Azure AD (production) |

Cả 2 cơ chế đều cấp cùng 1 loại token cuối cùng: **internal JWT** (`Bearer`),
dùng cho mọi Internal API còn lại (`Authorization: Bearer <access_token>`).

### 7.1. Kiến trúc SSO — SPA + Token Exchange

Frontend (React SPA) **tự thực hiện toàn bộ** luồng OpenID Connect
Authorization Code Flow + PKCE với Identity Provider (IdP) — Backend **không
tự redirect**, chỉ nhận `id_token` + `access_token` do FE đã lấy được, verify,
rồi cấp `internal JWT`.

```
┌──────────┐   1. Redirect (PKCE)    ┌───────────────┐
│  FE SPA  │ ──────────────────────> │  Keycloak /   │
│ (React)  │ <────────────────────── │  Azure AD     │
└────┬─────┘   2. code + state       └───────────────┘
     │ 3. Đổi code lấy id_token + access_token (gọi thẳng token_endpoint)
     │ 4. POST id_token + access_token
     v
┌──────────────┐   5. Verify JWKS, map user, cấp internal JWT
│  Backend     │
└──────────────┘
```

### 7.2. Endpoint `POST /api/v1/auth/azure/exchange`

**Request:**

```json
{
  "id_token": "<id_token từ Keycloak/Azure AD>",
  "access_token": "<access_token>"
}
```

**Xử lý** (`app/core/oidc.py` + `app/api/routes/auth.py`):

1. **Verify `id_token`** bằng `PyJWT` + `PyJWKClient` (tự lấy public key theo
   `kid` từ `AZURE_JWKS_URL`): kiểm tra chữ ký (`algorithms=["RS256"]` hard-code,
   **không** đọc `alg` từ header token — chống _algorithm confusion attack_),
   `issuer` khớp `AZURE_ISSUER`, `audience` khớp `AZURE_CLIENT_ID`.
2. **Verify `at_hash`**: hash của `access_token` phải khớp claim `at_hash`
   trong `id_token` — chống tráo đổi `access_token` giữa 2 phiên khác nhau.
3. **Đọc claims** theo cấu hình (`AZURE_ROLES_CLAIM`, `AZURE_PERMISSIONS_CLAIM`,
   `AZURE_ORG_CLAIM`) — hỗ trợ path lồng nhau (vd: `realm_access.roles` của
   Keycloak) để không cần đổi code khi chuyển sang Azure AD (claim phẳng `roles`).
4. **Map sang user nội bộ** (bảng `users`, tra theo `email`):
   - User đã tồn tại: dùng `roles`/`permissions` **lưu trong DB**, không tin
     lại claims token (chống escalation nếu claim IdP bị cấu hình sai). Chặn
     403 nếu `is_active=0`.
   - User chưa tồn tại: **JIT Provisioning** — tự tạo mới với role mặc định
     `guard` nếu token không có claim roles.
     > ⚠️ **Cần xác nhận lại với team trước production:** hiện tự động cấp
     > quyền cho tài khoản Azure AD/Keycloak chưa từng được duyệt nội bộ. Cân
     > nhắc đổi sang "từ chối, chờ admin duyệt thủ công" cho hệ thống kiểm
     > soát ra vào thật.
5. **Cấp `internal JWT`** bằng `create_internal_jwt()` (dùng lại nguyên hàm
   có sẵn cho dev-login) — response format giống hệt `dev-login`
   (`{status, access_token, user}`), FE không cần phân biệt xử lý.

### 7.3. Đăng xuất (Logout) — phân biệt 2 loại phiên

FE lưu key `sso_id_token` (khác `internal JWT`) trong `localStorage` **chỉ
khi** đăng nhập qua SSO. Khi đăng xuất:

- **Có `sso_id_token`** (đăng nhập qua SSO): redirect sang `end_session_endpoint`
  của IdP kèm `id_token_hint` — đăng xuất cả session Keycloak/Azure AD (**RP-
  Initiated Logout**). Nếu không làm bước này, user bấm "Đăng nhập SSO" lại
  sẽ tự động đăng nhập lại (session IdP vẫn sống) mà không hỏi mật khẩu.
- **Không có `sso_id_token`** (đăng nhập bằng `dev-login`): chỉ xóa token nội
  bộ, về thẳng `/login` — không có session IdP nào cần đăng xuất.

Yêu cầu cấu hình: client trên Keycloak/Azure AD phải khai báo **"Valid post
logout redirect URIs"** (khác whitelist dùng cho login), nếu không sẽ gặp lỗi
`"Invalid redirect uri"`.

### 7.4. Bảng mã lỗi Auth/SSO

| HTTP | `status` code                   | Khi nào                                                         | FE nên xử lý                                                              |
| ---- | ------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------- |
| 401  | `AUTH_INVALID_TOKEN`            | Chữ ký/iss/aud/at_hash sai                                      | Xóa token cũ, về `/login`                                                 |
| 401  | `TOKEN_EXPIRED`                 | `id_token` hết hạn trước khi verify                             | Yêu cầu đăng nhập SSO lại                                                 |
| 403  | `USER_INACTIVE`                 | User tồn tại nhưng `is_active=0`                                | Thông báo tài khoản bị khóa, **không** redirect lại `/login` (lặp vô hạn) |
| 403  | `PERMISSION_DENIED`             | Đã có `internal JWT`, thiếu quyền cụ thể (`require_permission`) | Phân biệt với 401 — lỗi phân quyền, không phải lỗi đăng nhập              |
| 501  | `AZURE_EXCHANGE_NOT_CONFIGURED` | `AZURE_JWKS_URL` chưa set trong `.env`                          | Lỗi cấu hình hạ tầng, báo DevOps                                          |

### 7.5. Mô hình đe dọa — các lớp bảo vệ đã có

| Tấn công                         | Phòng thủ                                                   | Vị trí                   |
| -------------------------------- | ----------------------------------------------------------- | ------------------------ |
| CSRF trên luồng OAuth            | So khớp `state`                                             | FE `SsoCallbackPage.tsx` |
| Đánh cắp `authorization code`    | PKCE (`code_verifier`/`code_challenge`)                     | FE `oidcPkce.ts`         |
| Algorithm confusion              | `algorithms=["RS256"]` hard-code                            | `app/core/oidc.py`       |
| Token cấp cho client khác        | Verify `audience`                                           | `app/core/oidc.py`       |
| Giả mạo issuer                   | Verify `issuer`                                             | `app/core/oidc.py`       |
| Tráo đổi `access_token`          | Verify `at_hash`                                            | `app/core/oidc.py`       |
| Escalation qua claim sai ở IdP   | User cũ dùng roles/permissions lưu DB, không tin claims mới | `app/api/routes/auth.py` |
| Replay `id_token` cũ             | **Chưa có** — cần bổ sung verify `nonce`                    | —                        |
| Tài khoản chưa duyệt tự có quyền | **Chưa có** — rủi ro của JIT Provisioning, xem 7.2 mục 4    | —                        |

### 7.6. Test case nên chạy sau mỗi lần sửa code Auth/SSO

| #   | Kịch bản                             | Kết quả mong đợi                                          |
| --- | ------------------------------------ | --------------------------------------------------------- |
| 1   | Đăng nhập SSO thành công             | Vào được trang chính, có `internal JWT`                   |
| 2   | User role không đủ quyền             | `403 PERMISSION_DENIED`, không bị đăng xuất               |
| 3   | JIT Provisioning — email mới         | Bảng `users` tự thêm dòng mới, role mặc định `guard`      |
| 4   | User đã tồn tại, claim token khác DB | `roles` cấp cho JWT lấy từ **DB**, không phải claims mới  |
| 5   | Tài khoản bị khóa (`is_active=0`)    | `403 USER_INACTIVE`                                       |
| 6   | `state` không khớp (giả lập CSRF)    | Từ chối, không gọi tiếp `/azure/exchange`                 |
| 7   | Token hết hạn                        | `401 TOKEN_EXPIRED`                                       |
| 8   | SSO giữa 2 app (cùng IdP)            | App 2 không hiện lại form login nếu App 1 đã đăng nhập    |
| 9   | Logout sau SSO                       | Đăng xuất cả Keycloak; login SSO lại phải hỏi mật khẩu    |
| 10  | Logout sau dev-login                 | Về thẳng `/login`, không redirect thừa qua IdP            |
| 11  | Thiếu `AZURE_JWKS_URL` trong `.env`  | `501 AZURE_EXCHANGE_NOT_CONFIGURED`, không phải crash 500 |

### 7.7. Checklist chuyển từ Keycloak (test) sang Azure AD thật

| Việc                                   | Ghi chú                                                                        |
| -------------------------------------- | ------------------------------------------------------------------------------ |
| Tạo App Registration trên Azure Portal | Lấy Application (client) ID, Directory (tenant) ID                             |
| Redirect URI loại "SPA"                | Trỏ đúng domain FE thật + `/auth/sso-callback`                                 |
| Front-channel logout URL               | Trỏ đúng domain FE thật + `/login`                                             |
| Tạo App roles, gán user/group          | Map vào claim `roles` (phẳng, khác Keycloak)                                   |
| Đổi `.env` Backend                     | `AZURE_ISSUER`, `AZURE_JWKS_URL`, `AZURE_CLIENT_ID`, `AZURE_ROLES_CLAIM=roles` |
| Đổi `.env` Frontend                    | `VITE_SSO_AUTHORITY`, `VITE_SSO_CLIENT_ID`                                     |
| Xác nhận chính sách JIT Provisioning   | Xem 7.2 mục 4                                                                  |
| Bổ sung verify `nonce`                 | Xem 7.5                                                                        |

---

## 8. Luồng test chính

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

## 9. Vé / QR thật

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

## 10. API cho FE

```txt
# Auth
POST /api/v1/auth/dev-login
POST /api/v1/auth/azure/exchange
GET  /api/v1/auth/me
GET  /api/v1/auth/dev-camera-token

# Nghiệp vụ ra/vào
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

## 11. SQLite DB

SQLite mặc định:

```txt
access_control.db
```

Đổi path DB:

```bash
ACCESS_DB_PATH=data/access_control.db uv run uvicorn app.main:app --reload
```

Bảng chính (nghiệp vụ ra/vào):

```txt
access_sessions
vehicle_access_logs
person_access_logs
tickets
ticket_print_logs
```

Bảng Auth (dùng cho dev-login + SSO):

```txt
organizations
users              -- cột azure_user_id dùng để map định danh SSO
camera_clients
camera_client_organizations
camera_tokens
```

---

## 12. Status chuẩn

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

## 13. Lệnh kiểm tra nhanh

Compile kiểm tra syntax:

```bash
uv run python -m py_compile app/main.py app/database.py app/core/oidc.py
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

## 14. Ràng buộc chống trùng / chống spam request

Bản này đã thêm các ràng buộc để tránh camera/FE gửi trùng làm tạo nhiều session và làm chậm hệ thống.

### 14.1. Chống trùng event xe

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

### 14.2. Chống OCR ảnh CCCD trùng

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

### 14.3. Chống face compare chạy lại

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

### 14.4. Checkout idempotent

Nếu session đã CHECKED_OUT mà gọi checkout lại, backend trả:

```txt
ALREADY_CHECKED_OUT
```

và không cập nhật lại dữ liệu.

### 14.5. DB constraint / index đã thêm

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

## 15. Lọc ảnh mặt CCCD, bỏ ảnh họa tiết/logo/QR

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
