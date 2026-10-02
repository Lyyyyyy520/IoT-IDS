"""
SQLite Database Layer — 7 tables for IoT IDS v2.0

Tables: users, alerts, traffic_logs, audit_logs, assets, policies, rules
"""
from contextlib import closing
from pathlib import Path
import sqlite3
from datetime import datetime
from flask import current_app, has_app_context
from werkzeug.security import generate_password_hash

from config import bootstrap_admin_password, bootstrap_admin_username

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',  -- admin/operator/user
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    risk_level TEXT NOT NULL,         -- critical/high/medium/low
    attack_type TEXT NOT NULL,        -- Mirai/Gafgyt/PortScan/BruteForce/DDoS/Other
    src_ip TEXT NOT NULL,
    dst_ip TEXT NOT NULL,
    src_port INTEGER,
    dst_port INTEGER,
    protocol TEXT,
    confidence REAL DEFAULT 0.0,
    description TEXT,
    raw_packet TEXT,                  -- hex-encoded raw packet data
    merged_count INTEGER DEFAULT 1,
    status TEXT DEFAULT 'new',        -- new/reviewed/resolved/false_positive
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS traffic_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    src_ip TEXT NOT NULL,
    dst_ip TEXT NOT NULL,
    src_port INTEGER,
    dst_port INTEGER,
    protocol TEXT,
    length INTEGER,
    flags TEXT,
    payload_hex TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT NOT NULL,
    action TEXT NOT NULL,             -- login/logout/block_ip/mark_fp/update_config/...
    detail TEXT,
    ip_address TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    mac_address TEXT,
    device_type TEXT,                 -- camera/door/sensor/router/hub/socket/lock/other
    status TEXT DEFAULT 'online',     -- online/offline/alert
    risk_level TEXT DEFAULT 'low',
    last_seen TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_type TEXT NOT NULL,        -- blacklist/whitelist/rule
    target TEXT NOT NULL,             -- IP address or rule pattern
    action TEXT NOT NULL DEFAULT 'alert',  -- alert/block/allow
    description TEXT,
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,           -- recon/exploit/c2/exfil/ddos/bruteforce
    pattern TEXT NOT NULL,            -- JSON or YAML rule pattern
    severity TEXT DEFAULT 'high',
    description TEXT,
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


class DatabaseUnavailableError(RuntimeError):
    """Raised when runtime access has no explicit, existing database file."""


def _existing_database_path(database_path=None) -> Path:
    if database_path is not None:
        raw_path = database_path
    elif has_app_context():
        raw_path = current_app.config.get("DATABASE_PATH")
    else:
        raise DatabaseUnavailableError(
            "database path requires an application context or explicit argument"
        )
    if raw_path is None or not str(raw_path).strip():
        raise DatabaseUnavailableError("IOT_IDS_DATABASE_PATH is not configured")
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise DatabaseUnavailableError("configured database file does not exist")
    return path.resolve()


def get_db(database_path=None):
    """Open an existing runtime database without allowing SQLite creation."""
    path = _existing_database_path(database_path)
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True)
    except sqlite3.Error as exc:
        raise DatabaseUnavailableError("configured database cannot be opened") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(database_path):
    """Explicit legacy initializer; never called by import or create_app()."""
    if database_path is None or not str(database_path).strip():
        raise ValueError("database_path is required for explicit initialization")
    path = Path(database_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)

    # Migration: add trace_info column if it doesn't exist
    try:
        conn.execute("ALTER TABLE alerts ADD COLUMN trace_info TEXT")
        print('[DB] Migration: added trace_info column to alerts')
    except sqlite3.OperationalError:
        pass

    # Migration: add source column to traffic_logs
    try:
        conn.execute("ALTER TABLE traffic_logs ADD COLUMN source TEXT DEFAULT 'sim'")
        print('[DB] Migration: added source column to traffic_logs')
    except sqlite3.OperationalError:
        pass

    # Migration: add onnx_label column
    try:
        conn.execute("ALTER TABLE traffic_logs ADD COLUMN onnx_label TEXT DEFAULT 'normal'")
        print('[DB] Migration: added onnx_label column to traffic_logs')
    except sqlite3.OperationalError:
        pass

    conn.commit()

    # Never seed a reusable password.  A first administrator is created only
    # when the operator supplies a one-time bootstrap password explicitly.
    admin_username = bootstrap_admin_username()
    admin_password = bootstrap_admin_password()
    admin_exists = conn.execute(
        "SELECT id FROM users WHERE username = ?", (admin_username,)
    ).fetchone()
    if admin_password and not admin_exists:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            (admin_username, generate_password_hash(admin_password)),
        )

    # Keep operator rows intact.  This is role normalization only; no v3 schema
    # or real data migration is performed during phase 0.
    conn.execute("UPDATE users SET role = 'admin' WHERE username = ?", (admin_username,))
    conn.execute(
        "UPDATE users SET role = 'user' "
        "WHERE username != ? AND role NOT IN ('operator', 'user')",
        (admin_username,),
    )

    conn.commit()
    conn.close()
    print(f'[DB] Initialized: {path}')


# ===== Query Helpers =====

def query_one(sql: str, params=()):
    """Run a SELECT query and return one row as dict, or None."""
    with closing(get_db()) as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(sql: str, params=()):
    """Run an INSERT/UPDATE/DELETE and return lastrowid."""
    with closing(get_db()) as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        last_id = cur.lastrowid
    return last_id
