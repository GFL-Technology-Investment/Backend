import os
import sqlite3
from typing import AsyncIterator, Optional, Dict, Any, List

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
    """Đưa access_sessions về schema mới:
    - Bỏ access_direction.
    - Thêm checked_in_at.
    - Thêm link_policy để khóa PERSON_ONLY sau face compare.

    Nếu DB cũ đã tồn tại từ bản trước, SQLite không hỗ trợ DROP COLUMN thuận tiện
    trên mọi phiên bản nên ta tạo bảng mới rồi copy dữ liệu cần giữ lại.
    """
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
            session_id,
            session_code,
            event_uid,
            linked_vehicle_event_uid,
            session_type,
            organization_id,
            location_id,
            gate_id,
            gate_name,
            status,
            link_policy,
            expected_plate_number,
            cccd_number,
            full_name,
            checked_in_at,
            checked_out_at,
            created_at,
            updated_at
        )
        SELECT
            session_id,
            session_code,
            event_uid,
            linked_vehicle_event_uid,
            session_type,
            organization_id,
            location_id,
            gate_id,
            gate_name,
            status,
            {link_policy_expr},
            expected_plate_number,
            cccd_number,
            full_name,
            {checked_in_expr},
            checked_out_at,
            created_at,
            updated_at
        FROM access_sessions
        """
    )
    conn.execute("DROP TABLE access_sessions")
    conn.execute("ALTER TABLE access_sessions_new RENAME TO access_sessions")
    conn.execute("PRAGMA foreign_keys = ON")


def migrate_tickets_schema(conn: sqlite3.Connection) -> None:
    """Bổ sung qr_value cho DB cũ nếu đã tạo tickets từ bản trước."""
    columns = get_table_columns(conn, "tickets")
    if not columns:
        return
    if "qr_value" not in columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN qr_value TEXT")


def migrate_person_logs_schema(conn: sqlite3.Connection) -> None:
    """Bổ sung cccd_image_hash để chống OCR submit trùng ảnh."""
    columns = get_table_columns(conn, "person_access_logs")
    if not columns:
        return
    if "cccd_image_hash" not in columns:
        conn.execute("ALTER TABLE person_access_logs ADD COLUMN cccd_image_hash TEXT")


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            ACCESS_SESSIONS_SCHEMA
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
            """
        )
        migrate_access_sessions_schema(conn)
        migrate_tickets_schema(conn)
        migrate_person_logs_schema(conn)
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_access_sessions_event_uid ON access_sessions(event_uid);
            CREATE INDEX IF NOT EXISTS idx_access_sessions_linked_vehicle_event_uid ON access_sessions(linked_vehicle_event_uid);
            CREATE INDEX IF NOT EXISTS idx_access_sessions_status ON access_sessions(status);
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
            """
        )
        conn.commit()
