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
        'WAITING_PERSON', 'WAITING_VEHICLE', 'WAITING_FACE_COMPARE',
        'CHECKED_IN', 'CHECKED_OUT', 'NEED_REVIEW', 'REJECTED', 'EXPIRED'
    )),
    link_policy TEXT NOT NULL DEFAULT 'ALLOW_VEHICLE_LINK' CHECK (
        link_policy IN ('ALLOW_VEHICLE_LINK', 'PERSON_ONLY_LOCKED')
    ),
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
