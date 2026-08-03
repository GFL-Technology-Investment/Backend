import sqlite3
import uuid
from app.core.config import settings
from app.core.security import hash_camera_token
from app.db.connection import (
    DB_PATH,
    get_connection,
    get_db,
    row_to_dict,
)
from app.db.schema import ACCESS_SESSIONS_SCHEMA, AUTH_SCHEMA
from app.db.migrations import (
    migrate_access_sessions_schema,
    migrate_legacy_user_roles,
    migrate_person_logs_schema,
    migrate_rbac_permissions,
    migrate_refresh_tokens_schema,
    migrate_tickets_schema,
    migrate_user_identity_providers,
    migrate_users_password_hash,
)
from app.db.seeds import (
    _PERMISSION_SEED,
    _ROLE_PERMISSION_SEED,
    _ROLE_SEED,
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
            '["admin"]',
            '["*"]',
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
                session_id TEXT,
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
            CREATE TABLE IF NOT EXISTS access_cards (
                card_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'AVAILABLE' CHECK (status IN ('AVAILABLE', 'IN_USE', 'DISABLED')),
                session_id TEXT,
                organization_id TEXT,
                linked_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES access_sessions(session_id)
            );
            """
        )

        migrate_access_sessions_schema(conn)
        migrate_tickets_schema(conn)
        migrate_person_logs_schema(conn)
        migrate_users_password_hash(conn)
        migrate_refresh_tokens_schema(conn)
        seed_rbac_data(conn)                    
        migrate_rbac_permissions(conn)
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
            CREATE INDEX IF NOT EXISTS idx_access_cards_status ON access_cards(status);
            CREATE INDEX IF NOT EXISTS idx_access_cards_session_id ON access_cards(session_id);

            CREATE INDEX IF NOT EXISTS idx_ticket_print_logs_ticket_id ON ticket_print_logs(ticket_id);

            CREATE INDEX IF NOT EXISTS idx_auth_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_auth_users_org ON users(organization_id);
            CREATE INDEX IF NOT EXISTS idx_auth_camera_tokens_hash ON camera_tokens(token_hash);
            CREATE INDEX IF NOT EXISTS idx_auth_camera_org ON camera_client_organizations(camera_client_id, organization_id);

            CREATE INDEX IF NOT EXISTS idx_refresh_tokens_hash    ON refresh_tokens(token_hash);
            CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id);
            CREATE INDEX IF NOT EXISTS idx_refresh_tokens_session_id ON refresh_tokens(session_id);
            CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires  ON refresh_tokens(expires_at);
            CREATE INDEX IF NOT EXISTS idx_user_identity_providers_user_id ON user_identity_providers(user_id);
            """
        )
        conn.commit()
