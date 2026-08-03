import os
import sqlite3
from typing import Any, AsyncIterator, Dict, List, Optional

DB_PATH = os.getenv("ACCESS_DB_PATH", "access_control.db")


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
