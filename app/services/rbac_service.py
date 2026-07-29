from __future__ import annotations

import sqlite3
import uuid
from typing import List, Tuple


def get_user_roles_and_permissions(db: sqlite3.Connection, user_id: str) -> Tuple[List[str], List[str]]:
    role_rows = db.execute(
        """
        SELECT r.role_code
        FROM user_roles ur
        JOIN roles r ON r.role_id = ur.role_id
        WHERE ur.user_id = ?
        """,
        (user_id,),
    ).fetchall()
    roles = [row["role_code"] for row in role_rows]

    permission_rows = db.execute(
        """
        SELECT DISTINCT p.permission_code
        FROM user_roles ur
        JOIN role_permissions rp ON rp.role_id = ur.role_id
        JOIN permissions p ON p.permission_id = rp.permission_id
        WHERE ur.user_id = ?
        """,
        (user_id,),
    ).fetchall()
    permissions = [row["permission_code"] for row in permission_rows]

    return roles, permissions


def assign_default_role(db: sqlite3.Connection, user_id: str, role_code: str = "GUARD") -> None:
    role_row = db.execute("SELECT role_id FROM roles WHERE role_code = ?", (role_code,)).fetchone()
    if not role_row:
        return

    db.execute(
        """
        INSERT OR IGNORE INTO user_roles (user_role_id, user_id, role_id, assigned_by, assigned_at)
        VALUES (?, ?, ?, 'jit_provisioning', CURRENT_TIMESTAMP)
        """,
        (str(uuid.uuid4()), user_id, role_row["role_id"]),
    )