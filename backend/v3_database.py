"""Additive, transactional SQLite migrations for the v3 data foundation.

The application-facing initializer still accepts an explicit path. The
operator CLI adds stricter existing-file and backup requirements around these
same migration definitions.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Iterable


MIGRATION_TABLE = "v3_schema_migrations"

MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS v3_schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    name TEXT NOT NULL UNIQUE,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
""".strip()

V3_DEVICE_STATE_STATEMENTS = (
    """
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
    )
    """.strip(),
    """
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
    )
    """.strip(),
    """
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
    )
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS v3_system_component_health (
        component_id TEXT PRIMARY KEY,
        readiness TEXT NOT NULL
            CHECK (readiness IN ('warming_up', 'ready', 'degraded')),
        started_at TEXT NOT NULL,
        ready_at TEXT,
        reason TEXT,
        state_version INTEGER NOT NULL DEFAULT 1 CHECK (state_version > 0),
        updated_at TEXT NOT NULL
    )
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_v3_observations_device_received
        ON v3_device_state_observations(device_id, received_at DESC)
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_v3_profiles_area
        ON v3_device_profiles(area_id)
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_v3_current_connection
        ON v3_device_current_state(connection_status)
    """.strip(),
)

# Retained as a readable schema representation for documentation and tooling.
V3_DEVICE_STATE_SCHEMA = ";\n\n".join(V3_DEVICE_STATE_STATEMENTS) + ";\n"


@dataclass(frozen=True)
class SchemaMigration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        material = "\n-- statement boundary --\n".join(self.statements)
        return sha256(material.encode("utf-8")).hexdigest()


V3_DEVICE_STATE_MIGRATION = SchemaMigration(
    version=1,
    name="device_state_foundation",
    statements=V3_DEVICE_STATE_STATEMENTS,
)

V3_MQTT_HEARTBEAT_STATEMENTS = (
    "ALTER TABLE v3_device_state_observations ADD COLUMN boot_id TEXT",
    "ALTER TABLE v3_device_state_observations ADD COLUMN firmware_version TEXT",
    "ALTER TABLE v3_device_state_observations ADD COLUMN uptime_ms INTEGER",
    """
    CREATE TABLE v3_mqtt_boot_sessions (
        device_id TEXT NOT NULL,
        boot_id TEXT NOT NULL,
        first_received_at TEXT NOT NULL,
        last_received_at TEXT NOT NULL,
        last_sequence INTEGER NOT NULL CHECK (last_sequence >= 0),
        last_uptime_ms INTEGER NOT NULL CHECK (last_uptime_ms >= 0),
        firmware_version TEXT NOT NULL,
        PRIMARY KEY (device_id, boot_id),
        FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id)
    )
    """.strip(),
    """
    CREATE TABLE v3_mqtt_device_cursors (
        device_id TEXT PRIMARY KEY,
        current_boot_id TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id),
        FOREIGN KEY (device_id, current_boot_id)
            REFERENCES v3_mqtt_boot_sessions(device_id, boot_id)
    )
    """.strip(),
    """
    CREATE UNIQUE INDEX idx_v3_mqtt_observation_sequence
        ON v3_device_state_observations(device_id, boot_id, sequence)
        WHERE boot_id IS NOT NULL
    """.strip(),
    """
    CREATE INDEX idx_v3_mqtt_sessions_last_received
        ON v3_mqtt_boot_sessions(last_received_at DESC)
    """.strip(),
)

V3_MQTT_HEARTBEAT_MIGRATION = SchemaMigration(
    version=2,
    name="mqtt_heartbeat_sessions",
    statements=V3_MQTT_HEARTBEAT_STATEMENTS,
)

V3_MIGRATIONS = (
    V3_DEVICE_STATE_MIGRATION,
    V3_MQTT_HEARTBEAT_MIGRATION,
)

V3_DEVICE_STATE_TABLES = frozenset(
    {
        "v3_device_profiles",
        "v3_device_current_state",
        "v3_device_state_observations",
        "v3_system_component_health",
    }
)

V3_DEVICE_STATE_INDEXES = frozenset(
    {
        "idx_v3_observations_device_received",
        "idx_v3_profiles_area",
        "idx_v3_current_connection",
    }
)

V3_MQTT_HEARTBEAT_TABLES = frozenset(
    {
        "v3_mqtt_boot_sessions",
        "v3_mqtt_device_cursors",
    }
)

V3_MQTT_HEARTBEAT_INDEXES = frozenset(
    {
        "idx_v3_mqtt_observation_sequence",
        "idx_v3_mqtt_sessions_last_received",
    }
)

V3_EXPECTED_OBJECTS = {
    MIGRATION_TABLE: "table",
    **{name: "table" for name in V3_DEVICE_STATE_TABLES},
    **{name: "table" for name in V3_MQTT_HEARTBEAT_TABLES},
    **{name: "index" for name in V3_DEVICE_STATE_INDEXES},
    **{name: "index" for name in V3_MQTT_HEARTBEAT_INDEXES},
}


class MigrationError(RuntimeError):
    """Raised when a migration cannot be applied atomically."""


def _utc_text(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise MigrationError("migration timestamp must be timezone-aware")
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _migration_table_exists(connection: sqlite3.Connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (MIGRATION_TABLE,),
    ).fetchone() is not None


def read_applied_migrations(connection: sqlite3.Connection) -> list[dict]:
    """Return migration ledger rows without creating the ledger."""
    if not _migration_table_exists(connection):
        return []
    rows = connection.execute(
        "SELECT version, name, checksum, applied_at "
        "FROM v3_schema_migrations ORDER BY version"
    ).fetchall()
    return [
        {
            "version": row[0],
            "name": row[1],
            "checksum": row[2],
            "applied_at": row[3],
        }
        for row in rows
    ]


def current_v3_schema_version(connection: sqlite3.Connection) -> int:
    applied = read_applied_migrations(connection)
    return max((row["version"] for row in applied), default=0)


def _integrity_results(connection: sqlite3.Connection) -> list[str]:
    return [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]


def apply_v3_migrations(
    connection: sqlite3.Connection,
    migrations: Iterable[SchemaMigration] = V3_MIGRATIONS,
    *,
    applied_at: datetime | None = None,
) -> dict:
    """Apply pending migrations in one transaction and record their checksums."""
    ordered = tuple(sorted(migrations, key=lambda item: item.version))
    if len({item.version for item in ordered}) != len(ordered):
        raise MigrationError("migration versions must be unique")
    timestamp = _utc_text(applied_at)
    applied_versions: list[int] = []
    skipped_versions: list[int] = []

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(MIGRATION_TABLE_SQL)
        ledger = {
            row["version"]: row
            for row in read_applied_migrations(connection)
        }

        for migration in ordered:
            existing = ledger.get(migration.version)
            if existing:
                if (
                    existing["name"] != migration.name
                    or existing["checksum"] != migration.checksum
                ):
                    raise MigrationError(
                        f"migration {migration.version} ledger checksum/name mismatch"
                    )
                skipped_versions.append(migration.version)
                continue

            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO v3_schema_migrations "
                "(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    timestamp,
                ),
            )
            applied_versions.append(migration.version)

        transaction_integrity = _integrity_results(connection)
        if transaction_integrity != ["ok"]:
            raise MigrationError(
                "post-migration integrity_check failed inside transaction: "
                + "; ".join(transaction_integrity)
            )
        connection.commit()
    except Exception as exc:
        connection.rollback()
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError(f"v3 migration transaction rolled back: {exc}") from exc

    return {
        "applied_versions": applied_versions,
        "skipped_versions": skipped_versions,
        "schema_version": current_v3_schema_version(connection),
        "transaction_integrity_check": transaction_integrity,
    }


def connect_v3(database_path: str | Path) -> sqlite3.Connection:
    """Open an explicit SQLite database with v3 safety settings enabled."""
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize_v3_database(database_path: str | Path) -> None:
    """Create and register additive v3 schema; safe to call repeatedly."""
    connection = connect_v3(database_path)
    try:
        apply_v3_migrations(connection)
    finally:
        connection.close()
