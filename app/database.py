import os
import sqlite3
import uuid
import json
from typing import AsyncIterator, Optional, Dict, Any, List

from app.core.config import settings
from app.core.security import hash_camera_token

DB_PATH = os.getenv("ACCESS_DB_PATH", "access_control.db")

ACCESS_SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS access_sessions (
    session_id TEXT PRIMARY KEY,
    session_code TEXT NOT NULL,
    event_uid TEXT UNIQUE NOT NULL,
    linked_vehicle_event_uid TEXT UNIQUE,

    session_type TEXT NOT NULL CHECK (session_type IN ('VEHICLE_WITH_PERSON', 'PERSON_ONLY')),
    organization_id TEXT NOT NULL,
    location_id TEXT,
    gate_id TEXT,
    gate_name TEXT,

    status TEXT NOT NULL CHECK (status IN (
        'WAITING_PERSON',
        'WAITING_VEHICLE',
        'WAITING_FACE_COMPARE',
        'CHECKED_IN',
        'CHECKED_OUT',
        'NEED_REVIEW',
        'REJECTED',
        'EXPIRED'
    )),
    link_policy TEXT NOT NULL DEFAULT 'ALLOW_VEHICLE_LINK' CHECK (link_policy IN (
        'ALLOW_VEHICLE_LINK',
        'PERSON_ONLY_LOCKED'
    )),
    expected_plate_number TEXT,
    cccd_number TEXT,
    full_name TEXT,

    checked_in_at TEXT,
    checked_out_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    organization_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    full_name TEXT,
    organization_id TEXT NOT NULL,
    roles TEXT NOT NULL DEFAULT '[]',
    permissions TEXT NOT NULL DEFAULT '[]',
    azure_user_id TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
);

CREATE TABLE IF NOT EXISTS roles (
    role_id TEXT PRIMARY KEY,
    role_code TEXT UNIQUE NOT NULL,
    role_name TEXT NOT NULL,
    description TEXT,
    is_system INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS permissions (
    permission_id TEXT PRIMARY KEY,
    permission_code TEXT UNIQUE NOT NULL,
    permission_name TEXT,
    module_name TEXT,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_role_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    assigned_by TEXT,
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, role_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES roles(role_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_permission_id TEXT PRIMARY KEY,
    role_id TEXT NOT NULL,
    permission_id TEXT NOT NULL,
    UNIQUE (role_id, permission_id),
    FOREIGN KEY (role_id) REFERENCES roles(role_id) ON DELETE CASCADE,
    FOREIGN KEY (permission_id) REFERENCES permissions(permission_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS camera_clients (
    camera_client_id TEXT PRIMARY KEY,
    client_code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    client_type TEXT NOT NULL DEFAULT 'CAMERA',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS camera_client_organizations (
    camera_client_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (camera_client_id, organization_id),
    FOREIGN KEY (camera_client_id) REFERENCES camera_clients(camera_client_id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS camera_tokens (
    token_id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_client_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL DEFAULT '["camera.event.write"]',
    expires_at TEXT,
    is_revoked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (camera_client_id) REFERENCES camera_clients(camera_client_id) ON DELETE CASCADE
);
"""

_ROLE_SEED = [
    ("ADMIN", "Admin", "Toàn quyền hệ thống", 1),
    ("MANAGER", "Manager", "Quản lý ", 1),
    ("GUARD", "Guard", "Bảo vệ trực cổng", 1),
]

_PERMISSION_SEED = [
    ("camera.view", "Xem camera", "camera"),
    ("camera.manage", "Quản lý camera", "camera"),
    ("ticket.issue", "Phát hành vé", "ticket"),
    ("ticket.print", "In vé", "ticket"),
    ("vehicle.approve", "Duyệt xe ra/vào", "vehicle"),
    ("report.export", "Xuất báo cáo", "report"),
    ("system.user.create", "Tạo user", "system"),
    ("system.user.update", "Sửa user", "system"),
    ("system.user.delete", "Xóa user", "system"),
    ("role.assign", "Gán role", "system"),
    ("permission.assign", "Gán permission", "system"),
]

_ROLE_PERMISSION_SEED = {
    "ADMIN": ["*"],
    "MANAGER": [
        "camera.view", "camera.manage", "ticket.issue", "ticket.print",
        "vehicle.approve", "report.export",
        "system.user.create", "system.user.update", "system.user.delete",
        "role.assign",
    ],

    "GUARD": ["camera.view", "ticket.issue", "ticket.print","vehicle.approve"],

}

_LEGACY_ROLE_MAP = {
    "admin": "ADMIN",
    "guard": "GUARD",
    "manager": "MANAGER",
}


def get_connection() -> sqlite3.Connection:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


async def get_db() -> AsyncIterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def get_table_columns(conn: sqlite3.Connection, table_name: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [row["name"] for row in rows]


def migrate_access_sessions_schema(conn: sqlite3.Connection) -> None:
    columns = get_table_columns(conn, "access_sessions")
    if not columns:
        return

    needs_migration = "access_direction" in columns or "checked_in_at" not in columns or "link_policy" not in columns
    if not needs_migration:
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS access_sessions_new (
            session_id TEXT PRIMARY KEY,
            session_code TEXT NOT NULL,
            event_uid TEXT UNIQUE NOT NULL,
            linked_vehicle_event_uid TEXT UNIQUE,

            session_type TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            location_id TEXT,
            gate_id TEXT,
            gate_name TEXT,

            status TEXT NOT NULL,
            link_policy TEXT NOT NULL DEFAULT 'ALLOW_VEHICLE_LINK',
            expected_plate_number TEXT,
            cccd_number TEXT,
            full_name TEXT,

            checked_in_at TEXT,
            checked_out_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )

    has_checked_in_at = "checked_in_at" in columns
    has_link_policy = "link_policy" in columns
    checked_in_expr = "checked_in_at" if has_checked_in_at else "CASE WHEN status = 'CHECKED_IN' THEN updated_at ELSE NULL END"
    link_policy_expr = "link_policy" if has_link_policy else "CASE WHEN session_type = 'PERSON_ONLY' AND status IN ('CHECKED_IN', 'CHECKED_OUT', 'NEED_REVIEW', 'REJECTED') THEN 'PERSON_ONLY_LOCKED' ELSE 'ALLOW_VEHICLE_LINK' END"

    conn.execute(
        f"""
        INSERT OR REPLACE INTO access_sessions_new (
            session_id, session_code, event_uid, linked_vehicle_event_uid,
            session_type, organization_id, location_id, gate_id, gate_name,
            status, link_policy, expected_plate_number, cccd_number, full_name,
            checked_in_at, checked_out_at, created_at, updated_at
        )
        SELECT
            session_id, session_code, event_uid, linked_vehicle_event_uid,
            session_type, organization_id, location_id, gate_id, gate_name,
            status, {link_policy_expr}, expected_plate_number, cccd_number, full_name,
            {checked_in_expr}, checked_out_at, created_at, updated_at
        FROM access_sessions
        """
    )
    conn.execute("DROP TABLE access_sessions")
    conn.execute("ALTER TABLE access_sessions_new RENAME TO access_sessions")
    conn.execute("PRAGMA foreign_keys = ON")


def migrate_tickets_schema(conn: sqlite3.Connection) -> None:
    columns = get_table_columns(conn, "tickets")
    if columns and "qr_value" not in columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN qr_value TEXT")


def migrate_user_identity_providers(conn: sqlite3.Connection) -> None:
    columns = get_table_columns(conn, "users")
    if not columns or "azure_user_id" not in columns:
        return

    rows = conn.execute(
        "SELECT user_id, email, azure_user_id FROM users WHERE azure_user_id IS NOT NULL AND azure_user_id != ''"
    ).fetchall()

    for row in rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_identity_providers
                (identity_id, user_id, provider, provider_sub, email_at_link)
            VALUES (?, ?, 'keycloak', ?, ?)
            """,
            (str(uuid.uuid4()), row["user_id"], row["azure_user_id"], row["email"]),
        )


def migrate_refresh_tokens_schema(conn: sqlite3.Connection) -> None:
    columns = get_table_columns(conn, "refresh_tokens")
    if columns and "rotated_at" not in columns:
        conn.execute("ALTER TABLE refresh_tokens ADD COLUMN rotated_at TEXT")


def migrate_person_logs_schema(conn: sqlite3.Connection) -> None:
    columns = get_table_columns(conn, "person_access_logs")
    if columns and "cccd_image_hash" not in columns:
        conn.execute("ALTER TABLE person_access_logs ADD COLUMN cccd_image_hash TEXT")

def migrate_users_password_hash(conn: sqlite3.Connection) -> None:
    columns = get_table_columns(conn, "users")
    if not columns:
        return
    if "password_hash" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")

def migrate_legacy_user_roles(conn: sqlite3.Connection) -> None:
    """Chuyển users.roles (JSON cũ) sang bảng user_roles chuẩn hóa."""
    rows = conn.execute("SELECT user_id, roles FROM users WHERE roles IS NOT NULL AND roles != ''").fetchall()

    for row in rows:
        try:
            legacy_roles = json.loads(row["roles"] or "[]")
        except (TypeError, ValueError):
            continue

        for legacy_role in legacy_roles:
            role_code = _LEGACY_ROLE_MAP.get(str(legacy_role).lower())
            if not role_code:
                continue

            role_row = conn.execute("SELECT role_id FROM roles WHERE role_code = ?", (role_code,)).fetchone()
            if not role_row:
                continue

            conn.execute(
                """
                INSERT OR IGNORE INTO user_roles (user_role_id, user_id, role_id, assigned_by, assigned_at)
                VALUES (?, ?, ?, 'system_migration', CURRENT_TIMESTAMP)
                """,
                (str(uuid.uuid4()), row["user_id"], role_row["role_id"]),
            )


def seed_rbac_data(conn: sqlite3.Connection) -> None:
    """Seed roles/permissions/role_permissions cố định"""
    for role_code, role_name, description, is_system in _ROLE_SEED:
        conn.execute(
            "INSERT OR IGNORE INTO roles (role_id, role_code, role_name, description, is_system) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), role_code, role_name, description, is_system),
        )

    for code, name, module in _PERMISSION_SEED:
        conn.execute(
            "INSERT OR IGNORE INTO permissions (permission_id, permission_code, permission_name, module_name) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), code, name, module),
        )

    all_permission_ids = [r["permission_id"] for r in conn.execute("SELECT permission_id FROM permissions").fetchall()]

    for role_code, permission_codes in _ROLE_PERMISSION_SEED.items():
        role_row = conn.execute("SELECT role_id FROM roles WHERE role_code = ?", (role_code,)).fetchone()
        if not role_row:
            continue

        target_ids = (
            all_permission_ids
            if permission_codes == ["*"]
            else [
                r["permission_id"]
                for code in permission_codes
                for r in conn.execute("SELECT permission_id FROM permissions WHERE permission_code = ?", (code,)).fetchall()
            ]
        )

        for permission_id in target_ids:
            conn.execute(
                "INSERT OR IGNORE INTO role_permissions (role_permission_id, role_id, permission_id) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), role_row["role_id"], permission_id),
            )


def seed_auth_data(conn: sqlite3.Connection) -> None:
    """Seed dữ liệu auth tối thiểu để test local."""
    conn.execute(
        "INSERT OR IGNORE INTO organizations (organization_id, name, is_active) VALUES (?, ?, 1)",
        (settings.default_organization_id, "Sân Nội Bài / Org test"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO organizations (organization_id, name, is_active) VALUES (?, ?, 1)",
        ("org-002", "Sân Tân Sơn Nhất / Org test"),
    )

    conn.execute(
        """
        INSERT OR REPLACE INTO users (
            user_id, email, full_name, organization_id, roles, permissions, azure_user_id, is_active, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        """,
        (
            "user-dev-001",
            "guard@company.com",
            "Guard Dev",
            settings.default_organization_id,
            '["guard"]',
            '["ocr.cccd.create","face.compare","ticket.issue","ticket.print","access.checkout","history.read","*"]',
            "azure-dev-user-001",
        ),
    )

    conn.execute(
        """
        INSERT OR REPLACE INTO camera_clients (
            camera_client_id, client_code, name, client_type, is_active, updated_at
        ) VALUES (?, ?, ?, 'CAMERA', 1, CURRENT_TIMESTAMP)
        """,
        (settings.dev_camera_client_id, settings.dev_camera_code, settings.dev_camera_name),
    )
    conn.execute(
        "INSERT OR IGNORE INTO camera_client_organizations (camera_client_id, organization_id) VALUES (?, ?)",
        (settings.dev_camera_client_id, settings.default_organization_id),
    )

    conn.execute(
        """
        INSERT OR REPLACE INTO camera_tokens (
            token_id, camera_client_id, token_hash, scope, expires_at, is_revoked, updated_at
        )
        VALUES (
            COALESCE((SELECT token_id FROM camera_tokens WHERE camera_client_id = ? LIMIT 1), NULL),
            ?, ?, '["camera.event.write"]', NULL, 0, CURRENT_TIMESTAMP
        )
        """,
        (
            settings.dev_camera_client_id,
            settings.dev_camera_client_id,
            hash_camera_token(settings.dev_camera_token),
        ),
    )


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            ACCESS_SESSIONS_SCHEMA
            + AUTH_SCHEMA
            + """
            CREATE TABLE IF NOT EXISTS vehicle_access_logs (
                event_uid TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,

                event_type TEXT NOT NULL CHECK (event_type IN ('VEHICLE_ACCESS')),
                source TEXT,
                plate_number TEXT NOT NULL,
                plate_confidence REAL,

                camera_id TEXT,
                camera_name TEXT,
                device_serial_number TEXT,

                plate_image_url TEXT,
                frame_image_url TEXT,
                driver_face_image_url TEXT,
                video_url TEXT,

                detected_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                FOREIGN KEY (session_id) REFERENCES access_sessions(session_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS person_access_logs (
                event_uid TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,

                event_type TEXT NOT NULL CHECK (event_type IN ('OCR_CCCD')),
                source TEXT,
                cccd_number TEXT,
                full_name TEXT,
                birth TEXT,
                sex TEXT,
                place TEXT,

                cccd_face_image_url TEXT,
                cccd_original_image_url TEXT,
                cccd_image_hash TEXT,
                live_face_image_url TEXT,
                live_face_source TEXT,

                face_compare_source TEXT,
                face_compare_score REAL,
                face_compare_threshold REAL,
                face_compare_result TEXT CHECK (face_compare_result IN ('PENDING', 'MATCH', 'NO_MATCH', 'NEED_REVIEW')),

                created_at TEXT NOT NULL,
                updated_at TEXT,

                FOREIGN KEY (session_id) REFERENCES access_sessions(session_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                ticket_code TEXT UNIQUE NOT NULL,
                ticket_type TEXT NOT NULL,

                front_image_url TEXT,
                back_image_url TEXT,
                qr_image_url TEXT,
                barcode_image_url TEXT,
                qr_value TEXT,
                barcode_value TEXT,

                status TEXT NOT NULL CHECK (status IN ('READY', 'PRINTED', 'CHECKED_OUT', 'CANCELLED')),
                issued_by TEXT,
                issued_at TEXT NOT NULL,
                printed_by TEXT,
                printed_at TEXT,
                checked_out_at TEXT,

                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                FOREIGN KEY (session_id) REFERENCES access_sessions(session_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ticket_print_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                printer_name TEXT,
                print_status TEXT NOT NULL CHECK (print_status IN ('SUCCESS', 'FAILED')),
                printed_by TEXT,
                printed_at TEXT NOT NULL,
                error_message TEXT,

                FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                audit_log_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL CHECK (event_type IN (
                    'OCR_CCCD', 'FACE_COMPARE', 'VEHICLE_DETECTED', 'VEHICLE_LINKED',
                    'CHECK_OUT', 'TICKET_ISSUED', 'TICKET_PRINTED', 'TICKET_CHECKOUT'
                )),
                session_id TEXT,
                event_uid TEXT,
                organization_id TEXT,
                gate_id TEXT,
                actor_type TEXT CHECK (actor_type IN ('CAMERA', 'GUARD', 'SYSTEM')),
                actor_id TEXT,
                result_status TEXT NOT NULL DEFAULT 'SUCCESS',
                detail TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS refresh_tokens (
                refresh_token_id TEXT PRIMARY KEY,
                user_id          TEXT NOT NULL,
                token_hash       TEXT NOT NULL UNIQUE,
                organization_id  TEXT NOT NULL,
                issued_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at       TEXT NOT NULL,
                is_revoked       INTEGER NOT NULL DEFAULT 0,
                replaced_by      TEXT,
                rotated_at       TEXT,
                created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS user_identity_providers (
                identity_id      TEXT PRIMARY KEY,
                user_id          TEXT NOT NULL,
                provider         TEXT NOT NULL CHECK (provider IN ('keycloak', 'azure')),
                provider_sub     TEXT NOT NULL,
                email_at_link    TEXT,
                linked_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

                UNIQUE (provider, provider_sub),
                UNIQUE (user_id, provider),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
            """
        )

        migrate_access_sessions_schema(conn)
        migrate_tickets_schema(conn)
        migrate_person_logs_schema(conn)
        migrate_users_password_hash(conn)
        migrate_refresh_tokens_schema(conn)
        seed_rbac_data(conn)                    
        seed_auth_data(conn)                    
        migrate_user_identity_providers(conn)   
        migrate_legacy_user_roles(conn)         

        # Tạo Index
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_access_sessions_event_uid ON access_sessions(event_uid);
            CREATE INDEX IF NOT EXISTS idx_access_sessions_linked_vehicle_event_uid ON access_sessions(linked_vehicle_event_uid);
            CREATE INDEX IF NOT EXISTS idx_access_sessions_status ON access_sessions(status);
            CREATE INDEX IF NOT EXISTS idx_user_roles_user_id ON user_roles(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_roles_role_id ON user_roles(role_id);
            CREATE INDEX IF NOT EXISTS idx_role_permissions_role_id ON role_permissions(role_id);
            CREATE INDEX IF NOT EXISTS idx_access_sessions_link_policy ON access_sessions(link_policy);
            CREATE INDEX IF NOT EXISTS idx_access_sessions_session_type ON access_sessions(session_type);
            CREATE INDEX IF NOT EXISTS idx_access_sessions_org_gate ON access_sessions(organization_id, gate_id);
            CREATE INDEX IF NOT EXISTS idx_access_sessions_cccd ON access_sessions(cccd_number);
            CREATE INDEX IF NOT EXISTS idx_access_sessions_expected_plate ON access_sessions(expected_plate_number);
            CREATE INDEX IF NOT EXISTS idx_access_sessions_created_at ON access_sessions(created_at);
            CREATE INDEX IF NOT EXISTS idx_access_sessions_checked_in_at ON access_sessions(checked_in_at);
            CREATE INDEX IF NOT EXISTS idx_access_sessions_checked_out_at ON access_sessions(checked_out_at);

            CREATE INDEX IF NOT EXISTS idx_vehicle_session_id ON vehicle_access_logs(session_id);
            CREATE INDEX IF NOT EXISTS idx_vehicle_plate_number ON vehicle_access_logs(plate_number);
            CREATE INDEX IF NOT EXISTS idx_vehicle_plate_session ON vehicle_access_logs(plate_number, session_id);
            CREATE INDEX IF NOT EXISTS idx_vehicle_camera_detected ON vehicle_access_logs(camera_id, detected_at);
            CREATE INDEX IF NOT EXISTS idx_vehicle_detected_at ON vehicle_access_logs(detected_at);

            CREATE INDEX IF NOT EXISTS idx_person_session_id ON person_access_logs(session_id);
            CREATE INDEX IF NOT EXISTS idx_person_cccd_number ON person_access_logs(cccd_number);
            CREATE INDEX IF NOT EXISTS idx_person_full_name ON person_access_logs(full_name);
            CREATE INDEX IF NOT EXISTS idx_person_cccd_image_hash ON person_access_logs(cccd_image_hash);
            CREATE INDEX IF NOT EXISTS idx_person_created_at ON person_access_logs(created_at);

            CREATE INDEX IF NOT EXISTS idx_tickets_session_id ON tickets(session_id);
            CREATE INDEX IF NOT EXISTS idx_tickets_ticket_code ON tickets(ticket_code);
            CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
            CREATE INDEX IF NOT EXISTS idx_tickets_issued_at ON tickets(issued_at);

            CREATE INDEX IF NOT EXISTS idx_ticket_print_logs_ticket_id ON ticket_print_logs(ticket_id);

            CREATE INDEX IF NOT EXISTS idx_auth_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_auth_users_org ON users(organization_id);
            CREATE INDEX IF NOT EXISTS idx_auth_camera_tokens_hash ON camera_tokens(token_hash);
            CREATE INDEX IF NOT EXISTS idx_auth_camera_org ON camera_client_organizations(camera_client_id, organization_id);

            CREATE INDEX IF NOT EXISTS idx_refresh_tokens_hash    ON refresh_tokens(token_hash);
            CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id);
            CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires  ON refresh_tokens(expires_at);
            CREATE INDEX IF NOT EXISTS idx_user_identity_providers_user_id ON user_identity_providers(user_id);
            """
        )
        conn.commit()