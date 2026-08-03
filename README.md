# OCR CCCD + Access Control API

Backend FastAPI cho hệ thống kiểm soát ra/vào bằng OCR CCCD, nhận diện khuôn mặt, camera/AIBox, vé QR/barcode và RBAC.

Hệ thống hiện phù hợp cho PoC, kiểm thử tích hợp và pilot nội bộ. Các endpoint có tiền tố `/mock` phục vụ mô phỏng camera/AIBox và cần được khóa hoặc tách khỏi môi trường production.

## Chức năng chính

- OCR CCCD bằng `gfl_core`.
- Lọc crop khuôn mặt trên ảnh CCCD bằng InsightFace.
- So sánh khuôn mặt người thật với ảnh CCCD.
- Quản lý session người, xe và trạng thái ra/vào.
- Hỗ trợ xe đến trước, người đến trước và `PERSON_ONLY`.
- Phát hành vé, QR code, barcode và checkout.
- SQLite lưu dữ liệu nghiệp vụ và refresh token hash.
- JWT nội bộ cho API người dùng.
- Redis tùy chọn cho session/revocation và kiểm tra refresh-token hash.
- SSO qua OpenID Connect với Keycloak hoặc Azure AD.
- RBAC chuẩn hóa qua `users -> user_roles -> roles -> role_permissions -> permissions`.

## Cấu trúc thư mục

```text
app/
  main.py
  api/
    router.py
    deps/auth.py
    routes/
      access.py
      aibox_mock.py
      auth.py
      cards.py
      face.py
      health.py
      history.py
      ocr.py
      organization.py
      permission.py
      role.py
      tickets.py
      user.py
  core/
    auth_context.py
    config.py
    files.py
    oidc.py
    redis.py
    security.py
    status.py
    time.py
  db/
    connection.py
    migrations.py
    schema.py
    seeds.py
  services/
    access_query_service.py
    access_service.py
    access_session_service.py
    audit_service.py
    card_service.py
    face_service.py
    login_service.py
    logout_service.py
    ocr_service.py
    oidc_service.py
    rbac_service.py
    refresh_service.py
    session_service.py
    ticket_renderer.py
    token_service.py
    user_revoke_service.py

gfl_core/        # OCR/model engine
static/          # media, ảnh CCCD, ảnh vé
uploads/         # file upload tạm
tests/           # unit/integration tests
pyproject.toml
uv.lock
```

`app/database.py` vẫn là facade tương thích cho các import cũ. Schema, connection, seed và migration đã được tách vào `app/db`.

## Yêu cầu môi trường

- Python `>=3.10,<3.14`.
- `uv`.
- Redis chỉ bắt buộc khi `REDIS_ENABLED=true`.
- Các dependency model như PyTorch, InsightFace, ONNX Runtime, OpenCV và VietOCR có thể cần nhiều thời gian cài đặt.

## Cài đặt

```bash
uv sync
```

Chạy bằng Python cụ thể:

```bash
uv python install 3.10
uv sync --python 3.10
```

## Cấu hình môi trường

Tạo file `.env` ở thư mục gốc:

```env
DEFAULT_ORGANIZATION_ID=org-001
DEFAULT_LOCATION_ID=loc-001
DEFAULT_GATE_ID=gate-001
DEFAULT_GATE_NAME=Cổng vào 01

AUTH_ENABLED=true
AUTH_DEV_MODE=true
INTERNAL_JWT_SECRET=change-this-secret
INTERNAL_JWT_ISSUER=gfl-core
INTERNAL_JWT_AUDIENCE=gfl-internal-api
INTERNAL_JWT_EXPIRE_SECONDS=900

REFRESH_TOKEN_EXPIRE_SECONDS=604800
REFRESH_TOKEN_HASH_PEPPER=change-this-refresh-pepper
REFRESH_TOKEN_ROTATION_GRACE_SECONDS=10
REFRESH_COOKIE_NAME=gfl_refresh_token
REFRESH_COOKIE_SECURE=false
REFRESH_COOKIE_SAMESITE=lax
REFRESH_COOKIE_PATH=/api/v1/auth

REDIS_ENABLED=false
REDIS_URL=redis://localhost:6379/0

CAMERA_TOKEN_HASH_PEPPER=change-this-camera-pepper
DEV_CAMERA_TOKEN=dev-camera-token

KEYCLOAK_ISSUER=
KEYCLOAK_JWKS_URL=
KEYCLOAK_CLIENT_ID=
KEYCLOAK_ROLES_CLAIM=realm_access.roles
KEYCLOAK_PERMISSIONS_CLAIM=permissions

AZURE_TENANT_ID=
AZURE_ISSUER=
AZURE_JWKS_URL=
AZURE_CLIENT_ID=
AZURE_ROLES_CLAIM=roles
AZURE_PERMISSIONS_CLAIM=permissions
```

Trong production:

- Đặt `AUTH_DEV_MODE=false`.
- Đổi toàn bộ secret/pepper.
- Đặt `REFRESH_COOKIE_SECURE=true` khi chạy HTTPS.
- Không expose các endpoint `/mock`.
- Dùng Redis HA nếu bật Redis session store.

## Chạy backend

```bash
uv run uvicorn app.main:app --reload
```

Mặc định:

```text
Swagger: http://127.0.0.1:8000/docs
Health:  http://127.0.0.1:8000/
```

Đổi vị trí SQLite:

PowerShell:

```powershell
$env:ACCESS_DB_PATH="data/access_control.db"
uv run uvicorn app.main:app --reload
```

Linux/macOS:

```bash
ACCESS_DB_PATH=data/access_control.db uv run uvicorn app.main:app --reload
```

## Authentication

### Login nội bộ

```http
POST /api/v1/auth/login
Content-Type: application/json
```

```json
{
  "email": "guard@company.com",
  "password": "123456"
}
```

Response cấp access JWT và set refresh token vào HttpOnly cookie.

### Refresh và logout

```text
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/auth/me
```

Refresh token:

- Chỉ lưu hash trong SQLite.
- Có rotation và reuse detection.
- Khi Redis bật, hash gửi lên phải khớp hash trong Redis session.
- Redis session được cập nhật sang hash mới sau rotation.

### SSO

```http
POST /api/v1/auth/azure/exchange
```

```json
{
  "provider": "keycloak",
  "id_token": "<id_token>",
  "access_token": "<access_token>",
  "org_id": "org-001"
}
```

Backend kiểm tra chữ ký, issuer, audience, `at_hash`, map user theo DB và cấp internal JWT.

## SQLite và Redis

SQLite là nguồn sự thật cho:

- refresh token rotation;
- `token_hash`;
- `expires_at`;
- `is_revoked`;
- `replaced_by`;
- `session_id`;
- role/permission mapping.

Redis là session/revocation store tùy chọn, lưu metadata session gồm:

- `session_id`;
- `user_id`;
- `organization_id`;
- roles/permissions;
- `refresh_token_hash`;
- `absolute_expires_at`.

Khi `REDIS_ENABLED=false`, login, refresh, logout và internal auth vẫn hoạt động theo SQLite/JWT policy.

Khi `REDIS_ENABLED=true` nhưng Redis không khả dụng:

- login/refresh trả lỗi có cấu trúc `503 AUTH_SESSION_STORE_UNAVAILABLE`;
- internal auth fail closed;
- không trả raw Redis exception;
- không log plaintext token hoặc full token hash.

SQLite và Redis không có distributed transaction tuyệt đối. Refresh flow commit SQLite trước, sau đó cập nhật Redis. Nếu Redis cập nhật thất bại, refresh token mới bị revoke/bù trừ theo policy trong `refresh_service.py`.

## RBAC

Permission được định nghĩa trong code và seed/migration. Người dùng không được tự tạo, sửa hoặc xóa permission runtime.

Permission hiện có cho quản trị role/permission:

```text
role.create
role.read
role.update
role.delete
role.assign
user.read

permission.read
permission.assign
```

| Permission | Nghiệp vụ |
|---|---|
| `role.read` | Xem danh sách/chi tiết role |
| `role.create` | Tạo role |
| `role.update` | Sửa role |
| `role.delete` | Xóa role |
| `role.assign` | Gán/thay role của user |
| `user.read` | Xem danh sách/chi tiết user |
| `permission.read` | Xem danh sách/chi tiết permission |
| `permission.assign` | Thay danh sách permission của role |

API permission chỉ còn:

```text
GET /api/v1/permissions
GET /api/v1/permissions/{permission_id}
```

Gán permission vào role:

```text
PUT /api/v1/roles/{role_id}/permissions
```

Các role/permission seed được thêm bằng `INSERT OR IGNORE`. Migration RBAC dọn các permission runtime cũ không còn hợp lệ và không xóa dữ liệu khác ngoài mapping được chủ động thay đổi.

## API chính

### Auth

```text
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/auth/me
GET  /api/v1/auth/dev-camera-token
POST /api/v1/auth/azure/exchange
```

### Kiểm soát ra/vào

```text
POST /ocr/cccd
POST /api/v1/face/compare
GET  /api/v1/access/history
GET  /api/v1/access/history/{event_uid}
POST /api/v1/access/checkout
```

### Vé và thẻ

```text
POST /api/v1/tickets/issue
GET  /api/v1/tickets
GET  /api/v1/tickets/{ticket_id}
GET  /api/v1/tickets/verify/{ticket_code}
POST /api/v1/tickets/{ticket_id}/print
POST /api/v1/tickets/checkout

POST /api/v1/cards/link
POST /api/v1/cards/checkout
GET  /api/v1/cards
```

### Quản trị

```text
GET/POST/PATCH/DELETE /api/v1/roles
GET/PUT               /api/v1/roles/{role_id}/permissions
GET                   /api/v1/list/user
GET/POST/PATCH/DELETE /api/v1/user[/{user_id}]
PUT                   /api/v1/user/{user_id}/roles
GET/POST/PATCH/DELETE /api/v1/organizations
GET                   /api/v1/permissions
```

Chi tiết request/response và permission tương ứng xem trong Swagger.

### Endpoint mock

```text
POST /mock/aibox/lpr-event
POST /mock/aibox/link-vehicle-to-person
POST /mock/aibox/link-vehicle-to-waiting-person
GET  /mock/access-sessions
GET  /mock/access-sessions/{event_uid}
```

Mock AIBox yêu cầu camera Bearer token và `X-Organization-ID`.

## Luồng nghiệp vụ

### Xe đến trước

```text
LPR event
  -> tạo vehicle session
  -> OCR CCCD
  -> face compare
  -> CHECKED_IN
  -> issue ticket
  -> checkout
```

### Người đến trước

```text
OCR CCCD + expected_plate_number
  -> WAITING_VEHICLE
  -> camera link vehicle
  -> face compare
  -> CHECKED_IN
  -> checkout
```

### PERSON_ONLY

```text
OCR CCCD
  -> face compare
  -> CHECKED_IN + PERSON_ONLY_LOCKED
  -> không cho ghép xe về sau
```

Session status:

```text
WAITING_PERSON
WAITING_VEHICLE
WAITING_FACE_COMPARE
CHECKED_IN
CHECKED_OUT
NEED_REVIEW
REJECTED
EXPIRED
```

## Database

SQLite mặc định:

```text
access_control.db
```

Bảng nghiệp vụ:

```text
access_sessions
vehicle_access_logs
person_access_logs
tickets
ticket_print_logs
audit_logs
access_cards
```

Bảng xác thực/RBAC:

```text
organizations
users
roles
permissions
user_roles
role_permissions
refresh_tokens
user_identity_providers
camera_clients
camera_client_organizations
camera_tokens
```

Migration được chạy idempotent trong `app/db/migrations.py` khi `init_db()` khởi tạo ứng dụng. Không cần xóa database hiện có.

## Test và kiểm tra

Chạy toàn bộ test:

```bash
uv run pytest
```

Chạy riêng nhóm auth/RBAC/Redis:

```bash
uv run pytest tests/test_auth_rbac_redis_integration.py tests/test_session_service.py -m integration
```

Kiểm tra syntax:

```bash
uv run python -m compileall app tests
```

Các nhóm test cần có:

- `REDIS_ENABLED=false`: login, refresh, logout, internal auth.
- Redis hoạt động: tạo session, hash đối chiếu, rotation, logout.
- Redis down: startup, login, refresh, internal auth và lỗi `503`.
- RBAC: đúng permission được phép, thiếu permission trả `403`.
- Refresh race/reuse detection.
- Khóa user: revoke SQLite và xóa Redis sessions.
- TTL Redis không vượt quá `expires_at`.

## Lưu ý production

Trước khi triển khai production cần hoàn thiện thêm:

- loại bỏ hoặc khóa toàn bộ endpoint `/mock`;
- loại bỏ `print()` chứa token, hash hoặc dữ liệu request;
- bổ sung logging/metrics/tracing;
- kiểm thử tải và race thực tế;
- backup/restore SQLite;
- Redis HA hoặc policy vận hành tương đương;
- xác nhận chính sách JIT provisioning SSO;
- bổ sung kiểm tra `nonce` cho OIDC;
- rà soát CORS, cookie Secure và secret management;
- cập nhật tài liệu FE theo response thực tế.
