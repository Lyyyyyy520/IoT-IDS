"""
SQLite Database Layer — 7 tables for IoT IDS v2.0

Tables: users, alerts, traffic_logs, audit_logs, assets, policies, rules
"""
import sqlite3
import os
from datetime import datetime
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'ids.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'readonly',  -- 'admin' or 'readonly'
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


def get_db():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Initialize database: create tables and seed default data."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
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

    # Seed default admin user if not exists
    existing = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ('admin', generate_password_hash('admin123'), 'admin'),
        )
        # Readonly guest account
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ('guest', generate_password_hash('guest123'), 'readonly'),
        )

    # Seed demo assets if empty
    asset_count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    if asset_count == 0:
        demo_assets = [
            ('摄像头-01', '192.168.1.10', '00:1a:2b:3c:4d:11', 'camera', 'online', 'low'),
            ('摄像头-02', '192.168.1.11', '00:1a:2b:3c:4d:12', 'camera', 'online', 'low'),
            ('门禁系统-01', '192.168.1.20', '00:1a:2b:3c:4d:21', 'door', 'online', 'low'),
            ('烟感传感器-01', '192.168.1.30', '00:1a:2b:3c:4d:31', 'sensor', 'online', 'low'),
            ('温湿度传感器-01', '192.168.1.31', '00:1a:2b:3c:4d:32', 'sensor', 'offline', 'low'),
            ('智能插座-01', '192.168.1.40', '00:1a:2b:3c:4d:41', 'socket', 'online', 'low'),
            ('智能网关', '192.168.1.40', '00:1a:2b:3c:4d:41', 'hub', 'online', 'low'),
            ('社区路由器', '192.168.1.1', '00:1a:2b:3c:4d:01', 'router', 'online', 'low'),
        ]
        conn.executemany(
            "INSERT INTO assets (name, ip_address, mac_address, device_type, status, risk_level, last_seen) VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))",
            demo_assets,
        )

    # Seed default config
    defaults = {
        'detection_mode': 'offline',
        'confidence_threshold': '0.85',
        'merge_window_minutes': '5',
        'auto_block': 'false',
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?,?)", (k, v))

    conn.commit()
    conn.close()
    print(f'[DB] Initialized: {DB_PATH}')


# ===== Query Helpers =====

def query_all(sql: str, params=()):
    """Run a SELECT query and return all rows as dicts."""
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_one(sql: str, params=()):
    """Run a SELECT query and return one row as dict, or None."""
    conn = get_db()
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else None


def execute(sql: str, params=()):
    """Run an INSERT/UPDATE/DELETE and return lastrowid."""
    conn = get_db()
    cur = conn.execute(sql, params)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id


def execute_many(sql: str, params_list):
    """Run executemany."""
    conn = get_db()
    conn.executemany(sql, params_list)
    conn.commit()
    conn.close()


def get_config(key: str, default=None):
    """Read a config value."""
    row = query_one("SELECT value FROM config WHERE key = ?", (key,))
    return row['value'] if row else default


def set_config(key: str, value):
    """Write a config value."""
    execute("INSERT OR REPLACE INTO config (key, value) VALUES (?,?)", (key, str(value)))
