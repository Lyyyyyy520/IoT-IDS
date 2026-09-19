"""Additive SQLite schema for the v3 device-state foundation.

The initializer receives an explicit database path so tests and maintenance
tools do not implicitly open the legacy production database.
"""
from pathlib import Path
import sqlite3


V3_DEVICE_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS v3_device_profiles (
    device_id TEXT PRIMARY KEY,
    identity_kind TEXT NOT NULL,
    identity_value TEXT NOT NULL,
    display_name TEXT NOT NULL,
    device_type TEXT NOT NULL,
    area_id TEXT,
    operation_mode TEXT NOT NULL DEFAULT 'active'
        CHECK (operation_mode IN ('active', 'maintenance', 'disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (identity_kind, identity_value)
);

CREATE TABLE IF NOT EXISTS v3_device_state_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    source TEXT NOT NULL,
    sequence INTEGER CHECK (sequence IS NULL OR sequence >= 0),
    ip_address TEXT,
    observed_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    payload_json TEXT,
    FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id),
    UNIQUE (device_id, source, sequence)
);

CREATE TABLE IF NOT EXISTS v3_device_current_state (
    device_id TEXT PRIMARY KEY,
    connection_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (connection_status IN ('unknown', 'online', 'stale', 'offline')),
    ip_address TEXT,
    last_observed_at TEXT,
    last_received_at TEXT,
    last_observation_id INTEGER,
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    updated_at TEXT NOT NULL,
    FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id),
    FOREIGN KEY (last_observation_id)
        REFERENCES v3_device_state_observations(observation_id)
);

CREATE TABLE IF NOT EXISTS v3_system_component_health (
    component_id TEXT PRIMARY KEY,
    readiness TEXT NOT NULL
        CHECK (readiness IN ('warming_up', 'ready', 'degraded')),
    started_at TEXT NOT NULL,
    ready_at TEXT,
    reason TEXT,
    state_version INTEGER NOT NULL DEFAULT 1 CHECK (state_version > 0),
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_v3_observations_device_received
    ON v3_device_state_observations(device_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_v3_profiles_area
    ON v3_device_profiles(area_id);
CREATE INDEX IF NOT EXISTS idx_v3_current_connection
    ON v3_device_current_state(connection_status);
"""


V3_DEVICE_STATE_TABLES = frozenset(
    {
        "v3_device_profiles",
        "v3_device_current_state",
        "v3_device_state_observations",
        "v3_system_component_health",
    }
)


def connect_v3(database_path: str | Path) -> sqlite3.Connection:
    """Open an explicit SQLite database with v3 safety settings enabled."""
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize_v3_database(database_path: str | Path) -> None:
    """Create only additive v3 tables and indexes; safe to call repeatedly."""
    connection = connect_v3(database_path)
    try:
        connection.executescript(V3_DEVICE_STATE_SCHEMA)
        connection.commit()
    finally:
        connection.close()
