import json
import sqlite3
import uuid

from app.db.connection import get_table_columns
from app.db.seeds import _LEGACY_ROLE_MAP, _PERMISSION_SEED


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
            session_id TEXT PRIMARY KEY, session_code TEXT NOT NULL,
            event_uid TEXT UNIQUE NOT NULL, linked_vehicle_event_uid TEXT UNIQUE,
            session_type TEXT NOT NULL CHECK (session_type IN ('VEHICLE_WITH_PERSON', 'PERSON_ONLY')),
            organization_id TEXT NOT NULL,
            location_id TEXT, gate_id TEXT, gate_name TEXT, status TEXT NOT NULL,
            link_policy TEXT NOT NULL DEFAULT 'ALLOW_VEHICLE_LINK' CHECK (
                link_policy IN ('ALLOW_VEHICLE_LINK', 'PERSON_ONLY_LOCKED')
            ),
            expected_plate_number TEXT, cccd_number TEXT, full_name TEXT,
            checked_in_at TEXT, checked_out_at TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    has_checked_in_at = "checked_in_at" in columns
    has_link_policy = "link_policy" in columns
    checked_in_expr = "checked_in_at" if has_checked_in_at else "CASE WHEN status = 'CHECKED_IN' THEN updated_at ELSE NULL END"
    link_policy_expr = (
        "link_policy" if has_link_policy
        else "CASE WHEN session_type = 'PERSON_ONLY' AND status IN ('CHECKED_IN', 'CHECKED_OUT', 'NEED_REVIEW', 'REJECTED') "
             "THEN 'PERSON_ONLY_LOCKED' ELSE 'ALLOW_VEHICLE_LINK' END"
    )
    conn.execute(
        f"""
        INSERT OR REPLACE INTO access_sessions_new (
            session_id, session_code, event_uid, linked_vehicle_event_uid,
            session_type, organization_id, location_id, gate_id, gate_name,
            status, link_policy, expected_plate_number, cccd_number, full_name,
            checked_in_at, checked_out_at, created_at, updated_at
        )
        SELECT session_id, session_code, event_uid, linked_vehicle_event_uid,
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
    if not columns:
        return
    if "rotated_at" not in columns:
        conn.execute("ALTER TABLE refresh_tokens ADD COLUMN rotated_at TEXT")
    if "session_id" not in columns:
        conn.execute("ALTER TABLE refresh_tokens ADD COLUMN session_id TEXT")


def migrate_person_logs_schema(conn: sqlite3.Connection) -> None:
    columns = get_table_columns(conn, "person_access_logs")
    if columns and "cccd_image_hash" not in columns:
        conn.execute("ALTER TABLE person_access_logs ADD COLUMN cccd_image_hash TEXT")


def migrate_users_password_hash(conn: sqlite3.Connection) -> None:
    columns = get_table_columns(conn, "users")
    if columns and "password_hash" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")


def migrate_legacy_user_roles(conn: sqlite3.Connection) -> None:
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
            if role_row:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO user_roles
                        (user_role_id, user_id, role_id, assigned_by, assigned_at)
                    VALUES (?, ?, ?, 'system_migration', CURRENT_TIMESTAMP)
                    """,
                    (str(uuid.uuid4()), row["user_id"], role_row["role_id"]),
                )


def migrate_rbac_permissions(conn: sqlite3.Connection) -> None:
    obsolete = conn.execute(
        """
        SELECT permission_id
        FROM permissions
        WHERE permission_code IN ('permission.create', 'permission.update', 'permission.delete')
        """
    ).fetchall()
    for row in obsolete:
        conn.execute("DELETE FROM role_permissions WHERE permission_id = ?", (row["permission_id"],))
    conn.execute(
        """
        DELETE FROM permissions
        WHERE permission_code IN ('permission.create', 'permission.update', 'permission.delete')
        """
    )

    for code, name, module in _PERMISSION_SEED:
        conn.execute(
            "INSERT OR IGNORE INTO permissions (permission_id, permission_code, permission_name, module_name) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), code, name, module),
        )
    manager = conn.execute("SELECT role_id FROM roles WHERE role_code = 'MANAGER'").fetchone()
    if not manager:
        return
    for code in (
        "user.read",
        "role.create", "role.read", "role.update", "role.delete", "role.assign",
        "permission.read", "permission.assign",
    ):
        permission = conn.execute(
            "SELECT permission_id FROM permissions WHERE permission_code = ?", (code,)
        ).fetchone()
        if permission:
            conn.execute(
                "INSERT OR IGNORE INTO role_permissions (role_permission_id, role_id, permission_id) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), manager["role_id"], permission["permission_id"]),
            )
