"""Explicit, backup-first retention maintenance for the v3 SQLite database.

This module never creates a source database. Planning uses a query-only,
read-only connection. Applying requires a pre-existing backup directory and
uses SQLite's backup API before deleting anything.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
import hashlib
from urllib.parse import quote
from pathlib import Path
import sqlite3
import sys
from typing import Callable, Mapping

from v3_database import (
    V3_EXPECTED_OBJECTS,
    V3_MIGRATIONS,
    read_applied_migrations,
)


EXIT_OK = 0
EXIT_INPUT = 2
EXIT_DATABASE = 3
EXIT_MAINTENANCE = 4
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 36500
DEFAULT_BATCH_LIMIT = 1000
MAX_BATCH_LIMIT = 100000

RETENTION_DEFAULTS = {
    "legacy_traffic_logs": 30,
    "realtime_events": 30,
    "device_observations": 90,
    "mqtt_boot_sessions": 180,
    "device_ip_bindings": 365,
    "traffic_aggregates": 365,
    "traffic_deduplication": 90,
    "unknown_device_observations": 180,
    "mobile_pairings": 30,
    "mobile_sessions": 90,
    "mobile_refresh_history": 90,
    "mobile_rate_limits": 2,
    "incident_timeline": 2555,
    "help_timeline": 2555,
    "help_requests": 2555,
    "mobile_user_confirmations": 2555,
    "mobile_notice_changes": 2555,
    "audit_records": 2555,
}


class MaintenanceError(RuntimeError):
    """A safe, stable maintenance failure without raw SQLite details."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class InputPathError(MaintenanceError):
    pass


class RetentionConfigurationError(MaintenanceError):
    pass


@dataclass(frozen=True)
class RetentionSettings:
    days: Mapping[str, int]

    def __post_init__(self) -> None:
        missing = set(RETENTION_DEFAULTS) - set(self.days)
        extra = set(self.days) - set(RETENTION_DEFAULTS)
        if missing or extra:
            raise RetentionConfigurationError(
                "retention_policy_set_invalid", "retention policy keys are invalid"
            )
        for key, value in self.days.items():
            if type(value) is not int or not MIN_RETENTION_DAYS <= value <= MAX_RETENTION_DAYS:
                raise RetentionConfigurationError(
                    "retention_days_out_of_range",
                    f"retention for {key} must be between {MIN_RETENTION_DAYS} and {MAX_RETENTION_DAYS}",
                )

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "RetentionSettings":
        source = os.environ if environment is None else environment
        settings: dict[str, int] = {}
        for key, default in RETENTION_DEFAULTS.items():
            env_key = "IOT_IDS_RETENTION_" + key.upper() + "_DAYS"
            raw = source.get(env_key, str(default)).strip()
            try:
                value = int(raw)
            except (TypeError, ValueError) as exc:
                raise RetentionConfigurationError(
                    "retention_days_invalid",
                    f"{env_key} must be an integer",
                ) from exc
            if not MIN_RETENTION_DAYS <= value <= MAX_RETENTION_DAYS:
                raise RetentionConfigurationError(
                    "retention_days_out_of_range",
                    f"{env_key} must be between {MIN_RETENTION_DAYS} and {MAX_RETENTION_DAYS}",
                )
            settings[key] = value
        return cls(settings)


@dataclass(frozen=True)
class RetentionRule:
    key: str
    table: str
    time_column: str
    protection_sql: str = "1=1"
    protection_reason: str = "no_current_or_referenced_rows"
    description: str = "expired history"


RULES = (
    RetentionRule(
        "legacy_traffic_logs", "traffic_logs", "timestamp",
        description="expired_raw_traffic",
    ),
    RetentionRule(
        "device_observations", "v3_device_state_observations", "received_at",
        "NOT EXISTS (SELECT 1 FROM v3_device_current_state s "
        "WHERE s.last_observation_id = v3_device_state_observations.observation_id) "
        "AND NOT EXISTS (SELECT 1 FROM v3_device_ip_bindings b "
        "WHERE b.source_observation_id = v3_device_state_observations.observation_id)",
        "current_device_state_or_ip_binding_reference",
        "unreferenced_device_observation_history",
    ),
    RetentionRule(
        "mqtt_boot_sessions", "v3_mqtt_boot_sessions", "last_received_at",
        "NOT EXISTS (SELECT 1 FROM v3_mqtt_device_cursors c "
        "WHERE c.device_id = v3_mqtt_boot_sessions.device_id "
        "AND c.current_boot_id = v3_mqtt_boot_sessions.boot_id)",
        "current_mqtt_cursor_reference",
        "obsolete_mqtt_boot_session",
    ),
    RetentionRule(
        "device_ip_bindings", "v3_device_ip_bindings", "valid_to",
        "valid_to IS NOT NULL",
        "open_current_ip_binding",
        "closed_ip_binding_history",
    ),
    RetentionRule(
        "traffic_aggregates", "v3_device_traffic_minutes", "bucket_start",
        description="expired_traffic_aggregate",
    ),
    RetentionRule(
        "traffic_aggregates", "v3_device_traffic_protocol_minutes", "bucket_start",
        description="expired_protocol_traffic_aggregate",
    ),
    RetentionRule(
        "traffic_aggregates", "v3_device_traffic_peer_minutes", "bucket_start",
        description="expired_peer_traffic_aggregate",
    ),
    RetentionRule(
        "traffic_aggregates", "v3_traffic_unassigned_minutes", "bucket_start",
        description="expired_unassigned_traffic_aggregate",
    ),
    RetentionRule(
        "unknown_device_observations", "v3_discovery_observations", "received_at",
        "EXISTS (SELECT 1 FROM v3_discovered_device_candidates c "
        "WHERE c.candidate_id = v3_discovery_observations.candidate_id "
        "AND c.status IN ('claimed','ignored'))",
        "unresolved_discovery_candidate",
        "resolved_discovery_observation_history",
    ),
    RetentionRule(
        "mobile_pairings", "v3_mobile_pairings", "expires_at",
        "(expires_at <= ? AND (claimed_at IS NULL OR julianday(claimed_at) < julianday(?)) "
        "AND (invalidated_at IS NULL OR julianday(invalidated_at) < julianday(?)))",
        "valid_or_recently_claimed_pairing",
        "expired_pairing_history",
    ),
    RetentionRule(
        "mobile_sessions", "v3_mobile_sessions", "access_expires_at",
        "(julianday(v3_mobile_sessions.refresh_expires_at) < julianday(?) AND "
        "(v3_mobile_sessions.revoked_at IS NULL OR "
        "julianday(v3_mobile_sessions.revoked_at) < julianday(?)) AND "
        "NOT EXISTS (SELECT 1 FROM v3_mobile_refresh_history h "
        "WHERE h.session_id=v3_mobile_sessions.session_id) AND "
        "NOT EXISTS (SELECT 1 FROM v3_help_requests hr "
        "WHERE hr.mobile_session_id=v3_mobile_sessions.session_id))",
        "valid_session_or_refresh_history_or_help_request_reference",
        "expired_mobile_session",
    ),
    RetentionRule(
        "mobile_rate_limits", "v3_mobile_rate_limits", "updated_at",
        "(blocked_until IS NULL OR julianday(blocked_until) <= julianday(?))",
        "active_rate_limit_block",
        "expired_rate_limit_bucket",
    ),
    RetentionRule(
        "incident_timeline", "v3_incident_timeline", "occurred_at",
        "EXISTS (SELECT 1 FROM v3_incidents i "
        "WHERE i.incident_id = v3_incident_timeline.incident_id "
        "AND i.status IN ('resolved','false_positive'))",
        "active_incident_evidence",
        "completed_incident_timeline",
    ),
    RetentionRule(
        "help_timeline", "v3_help_request_timeline", "occurred_at",
        "EXISTS (SELECT 1 FROM v3_help_requests h "
        "WHERE h.help_request_id = v3_help_request_timeline.help_request_id "
        "AND h.status = 'closed' AND h.closed_at IS NOT NULL "
        "AND julianday(h.closed_at) <= julianday(?))",
        "unfinished_help_request",
        "closed_help_request_timeline",
    ),
    RetentionRule(
        "help_requests", "v3_help_requests", "closed_at",
        "status = 'closed' AND NOT EXISTS (SELECT 1 FROM v3_help_request_timeline t "
        "WHERE t.help_request_id = v3_help_requests.help_request_id)",
        "unfinished_help_request_or_timeline_reference",
        "closed_help_request_record",
    ),
    RetentionRule(
        "mobile_user_confirmations", "v3_mobile_notice_acknowledgements", "updated_at",
        "EXISTS (SELECT 1 FROM v3_incidents i "
        "WHERE i.incident_id = v3_mobile_notice_acknowledgements.incident_id "
        "AND i.status IN ('resolved','false_positive'))",
        "active_incident_confirmation",
        "completed_incident_confirmation",
    ),
    RetentionRule(
        "audit_records", "audit_logs", "created_at",
        description="legacy_admin_audit",
    ),
    RetentionRule(
        "audit_records", "v3_device_management_audit", "occurred_at",
        description="device_management_audit",
    ),
    RetentionRule(
        "audit_records", "v3_mobile_security_audit", "occurred_at",
        description="mobile_security_audit",
    ),
    RetentionRule(
        "audit_records", "v3_incident_workflow_audit", "occurred_at",
        description="incident_workflow_audit",
    ),
    RetentionRule(
        "audit_records", "v3_discovery_actions", "occurred_at",
        description="discovery_action_audit",
    ),
)


def _utc(value: datetime | None) -> datetime:
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise MaintenanceError("now_timezone_required", "--now must include a timezone")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _cutoff(now: datetime, days: int) -> str:
    return _iso(now - timedelta(days=days))


def _resolve_existing(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise InputPathError(
            "database_file_missing", "database file does not exist; refusing to create it"
        )
    return resolved


def _check_sidecars(path: Path) -> None:
    sidecars = tuple(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))
    if any(item.exists() for item in sidecars):
        raise InputPathError(
            "database_sidecar_present",
            "SQLite sidecar is present; stop writers and use an isolated maintenance copy",
        )


def _header_journal_mode(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            header = handle.read(20)
    except OSError as exc:
        raise MaintenanceError("database_io_error", "database header cannot be read") from exc
    if len(header) < 20 or header[:16] != b"SQLite format 3\x00":
        raise MaintenanceError("database_corrupt", "database header is invalid")
    versions = header[18:20]
    if versions == b"\x01\x01":
        return "rollback"
    if versions == b"\x02\x02":
        return "wal"
    raise MaintenanceError("database_journal_mode_unknown", "database journal mode cannot be verified")


def _check_apply_sidecars(path: Path) -> None:
    journal = Path(str(path) + "-journal")
    wal = Path(str(path) + "-wal")
    shm = Path(str(path) + "-shm")
    if journal.exists():
        raise InputPathError(
            "database_journal_present",
            "rollback journal is present; preserve it and recover an isolated copy first",
        )
    if wal.exists() != shm.exists():
        raise InputPathError(
            "database_wal_sidecar_incomplete",
            "WAL and shared-memory sidecars are inconsistent; recover an isolated copy first",
        )


def _sqlite_error_code(error: sqlite3.Error) -> str:
    code = getattr(error, "sqlite_errorcode", None)
    primary = (int(code) & 0xFF) if isinstance(code, int) else None
    message = str(error).lower()
    if primary is None:
        if "database or disk is full" in message or "disk is full" in message:
            return "database_disk_full"
        if "database is locked" in message or "database table is locked" in message:
            return "database_locked"
        if "database is busy" in message:
            return "database_busy"
        if "readonly" in message or "read-only" in message:
            return "database_read_only"
    if primary == sqlite3.SQLITE_BUSY:
        return "database_busy"
    if primary == sqlite3.SQLITE_LOCKED:
        return "database_locked"
    if primary == sqlite3.SQLITE_READONLY:
        return "database_read_only"
    if primary == sqlite3.SQLITE_FULL:
        return "database_disk_full"
    if primary in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
        return "database_corrupt"
    if primary == sqlite3.SQLITE_CANTOPEN:
        return "database_open_failed"
    if primary == sqlite3.SQLITE_IOERR:
        return "database_io_error"
    return "database_operation_failed"


def _sqlite_uri(path: Path, mode: str, *, immutable: bool = False) -> str:
    encoded = quote(path.resolve().as_posix(), safe="/:")
    options = f"mode={mode}"
    if immutable:
        options += "&immutable=1"
    return f"file:{encoded}?{options}"


def _file_fingerprint(path: Path) -> tuple[int, int, str]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, digest.hexdigest()


def _open_readonly(path: Path, *, immutable: bool = False, strict: bool = True) -> sqlite3.Connection:
    if strict:
        _check_sidecars(path)
    try:
        connection = sqlite3.connect(
            _sqlite_uri(path, "ro", immutable=immutable), uri=True, timeout=0.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection
    except MaintenanceError:
        raise
    except sqlite3.Error as exc:
        raise MaintenanceError(_sqlite_error_code(exc), "cannot open database read-only") from exc


def _integrity_ok(connection: sqlite3.Connection) -> bool:
    try:
        return all(row[0] == "ok" for row in connection.execute("PRAGMA integrity_check"))
    except sqlite3.Error:
        return False


def _validate_schema(connection: sqlite3.Connection) -> int:
    try:
        applied = read_applied_migrations(connection)
        ledger = {int(row["version"]): row for row in applied}
        version = max(ledger, default=0)
        if version != 9:
            raise MaintenanceError(
                "schema_version_unsupported", "maintenance requires schema version 9"
            )
        for migration in V3_MIGRATIONS:
            row = ledger.get(migration.version)
            if row is None:
                raise MaintenanceError("schema_incomplete", "database schema is incomplete")
            if row["name"] != migration.name or row["checksum"] != migration.checksum:
                raise MaintenanceError(
                    "schema_checksum_mismatch", "database migration checksum mismatch"
                )
        objects = {
            row["name"]: row["type"]
            for row in connection.execute(
                "SELECT name,type FROM sqlite_master WHERE type IN ('table','index')"
            )
        }
        if any(objects.get(name) != kind for name, kind in V3_EXPECTED_OBJECTS.items()):
            raise MaintenanceError("schema_object_missing", "database schema objects are incomplete")
        return version
    except MaintenanceError:
        raise
    except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        raise MaintenanceError("schema_invalid", "database schema cannot be verified") from exc


def _rule_parameters(rule: RetentionRule, cutoff: str, now: str) -> tuple[str, ...]:
    placeholders = rule.protection_sql.count("?")
    if rule.key == "mobile_pairings":
        protection_parameters = (now, cutoff, cutoff)
    elif rule.key == "mobile_sessions":
        protection_parameters = (cutoff, cutoff)
    elif rule.key == "mobile_rate_limits":
        protection_parameters = (now,)
    elif rule.key == "help_timeline":
        protection_parameters = (cutoff,)
    else:
        protection_parameters = ()
    if len(protection_parameters) != placeholders:
        raise RuntimeError("retention rule parameter mismatch")
    return (cutoff, *protection_parameters)


def _active_incident_ids(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT incident_id FROM v3_incidents "
            "WHERE status IN ('open','acknowledged','recovering')"
        )
    }


def _event_prefix(
    connection: sqlite3.Connection, cutoff: str, active_incidents: set[str]
) -> tuple[int, str | None, str | None, int]:
    eligible = 0
    first = None
    last = None
    protected = 0
    previous_id = None
    for row in connection.execute(
        "SELECT event_id,event_type,occurred_at,payload_json "
        "FROM v3_realtime_events ORDER BY event_id"
    ):
        event_id = int(row["event_id"])
        if previous_id is not None and event_id != previous_id + 1:
            break
        previous_id = event_id
        timestamp = row["occurred_at"]
        expired = connection.execute(
            "SELECT julianday(?) < julianday(?)", (timestamp, cutoff)
        ).fetchone()[0]
        if not expired:
            break
        if row["event_type"].startswith("incident."):
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict) and payload.get("incident_id") in active_incidents:
                protected += 1
                break
        if first is None:
            first = timestamp
        last = timestamp
        eligible += 1
    return eligible, first, last, protected


def _event_candidates(
    connection: sqlite3.Connection,
    cutoff: str,
    active_incidents: set[str],
    limit: int,
) -> list[int]:
    ids: list[int] = []
    previous_id = None
    for row in connection.execute(
        "SELECT event_id,event_type,occurred_at,payload_json "
        "FROM v3_realtime_events ORDER BY event_id"
    ):
        event_id = int(row["event_id"])
        if previous_id is not None and event_id != previous_id + 1:
            break
        previous_id = event_id
        expired = connection.execute(
            "SELECT julianday(?) < julianday(?)", (row["occurred_at"], cutoff)
        ).fetchone()[0]
        if not expired:
            break
        if row["event_type"].startswith("incident."):
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict) and payload.get("incident_id") in active_incidents:
                break
        ids.append(event_id)
        if len(ids) >= limit:
            break
    return ids


def _notice_change_prefix(
    connection: sqlite3.Connection,
    cutoff: str,
    limit: int | None = None,
) -> tuple[list[int], int, str | None, str | None]:
    ids: list[int] = []
    old_count = int(connection.execute(
        "SELECT COUNT(*) FROM v3_mobile_notice_changes "
        "WHERE julianday(changed_at) < julianday(?)", (cutoff,),
    ).fetchone()[0])
    oldest = newest = None
    previous_id = None
    for row in connection.execute(
        "SELECT c.change_id,c.incident_id,c.changed_at,i.status "
        "FROM v3_mobile_notice_changes c "
        "LEFT JOIN v3_incidents i ON i.incident_id=c.incident_id "
        "ORDER BY c.change_id"
    ):
        change_id = int(row["change_id"])
        if previous_id is not None and change_id != previous_id + 1:
            break
        previous_id = change_id
        is_expired = connection.execute(
            "SELECT julianday(?) < julianday(?)", (row["changed_at"], cutoff)
        ).fetchone()[0]
        if not is_expired or row["status"] not in {"resolved", "false_positive"}:
            break
        ids.append(change_id)
        oldest = row["changed_at"] if oldest is None else oldest
        newest = row["changed_at"]
        if limit is not None and len(ids) >= limit:
            break
    return ids, old_count, oldest, newest


def _rule_summary(
    connection: sqlite3.Connection,
    rule: RetentionRule,
    cutoff: str,
    now: str,
) -> tuple[int, int, str | None, str | None]:
    table = rule.table
    column = rule.time_column
    params = _rule_parameters(rule, cutoff, now)
    old_count = int(connection.execute(
        f"SELECT COUNT(*) FROM {table} WHERE julianday({column}) < julianday(?)",
        (cutoff,),
    ).fetchone()[0])
    eligible = int(connection.execute(
        f"SELECT COUNT(*) FROM {table} WHERE julianday({column}) < julianday(?) "
        f"AND ({rule.protection_sql})",
        params,
    ).fetchone()[0])
    bounds = connection.execute(
        f"SELECT MIN({column}),MAX({column}) FROM {table} "
        f"WHERE julianday({column}) < julianday(?) AND ({rule.protection_sql})",
        params,
    ).fetchone()
    return old_count, eligible, bounds[0], bounds[1]


def _ingest_batch_summary(
    connection: sqlite3.Connection, cutoff: str
) -> tuple[int, int, str | None, str | None]:
    where = (
        "julianday(b.received_at) < julianday(?) AND EXISTS ("
        "SELECT 1 FROM v3_traffic_ingest_batches newest "
        "WHERE newest.source_id=b.source_id "
        "AND newest.source_session_id=b.source_session_id "
        "AND newest.batch_sequence > b.batch_sequence)"
    )
    eligible = int(connection.execute(
        f"SELECT COUNT(*) FROM v3_traffic_ingest_batches b WHERE {where}", (cutoff,)
    ).fetchone()[0])
    oldest, newest = connection.execute(
        f"SELECT MIN(b.received_at),MAX(b.received_at) "
        f"FROM v3_traffic_ingest_batches b WHERE {where}", (cutoff,)
    ).fetchone()
    old_count = int(connection.execute(
        "SELECT COUNT(*) FROM v3_traffic_ingest_batches "
        "WHERE julianday(received_at) < julianday(?)", (cutoff,)
    ).fetchone()[0])
    return old_count, eligible, oldest, newest


def _build_plan(
    connection: sqlite3.Connection,
    now: datetime,
    settings: RetentionSettings,
    batch_limit: int,
) -> dict:
    now_text = _iso(now)
    active_incidents = _active_incident_ids(connection)
    table_rows = []
    for rule in RULES:
        days = settings.days[rule.key]
        cutoff = _cutoff(now, days)
        old_count, eligible, oldest, newest = _rule_summary(
            connection, rule, cutoff, now_text
        )
        table_rows.append({
            "policy": rule.key,
            "table": rule.table,
            "retention_days": days,
            "cutoff_at": cutoff,
            "eligible_rows": eligible,
            "planned_rows": min(eligible, batch_limit),
            "protected_rows": max(0, old_count - eligible),
            "oldest_eligible_at": oldest,
            "newest_eligible_at": newest,
            "selection_reason_code": rule.description,
            "protection_reason_code": rule.protection_reason,
        })

    notice_days = settings.days["mobile_notice_changes"]
    notice_cutoff = _cutoff(now, notice_days)
    notice_ids, old_notice_count, notice_oldest, notice_newest = _notice_change_prefix(
        connection, notice_cutoff
    )
    table_rows.append({
        "policy": "mobile_notice_changes",
        "table": "v3_mobile_notice_changes",
        "retention_days": notice_days,
        "cutoff_at": notice_cutoff,
        "eligible_rows": len(notice_ids),
        "planned_rows": min(len(notice_ids), batch_limit),
        "protected_rows": max(0, old_notice_count - len(notice_ids)),
        "oldest_eligible_at": notice_oldest,
        "newest_eligible_at": notice_newest,
        "selection_reason_code": "expired_completed_notice_change_prefix",
        "protection_reason_code": "notice_cursor_continuity_and_active_incident_evidence",
    })

    event_days = settings.days["realtime_events"]
    event_cutoff = _cutoff(now, event_days)
    event_count, oldest, newest, protected_event_count = _event_prefix(
        connection, event_cutoff, active_incidents
    )
    table_rows.append({
        "policy": "realtime_events",
        "table": "v3_realtime_events",
        "retention_days": event_days,
        "cutoff_at": event_cutoff,
        "eligible_rows": event_count,
        "planned_rows": min(event_count, batch_limit),
        "protected_rows": protected_event_count,
        "oldest_eligible_at": oldest,
        "newest_eligible_at": newest,
        "selection_reason_code": "expired_oldest_event_prefix",
        "protection_reason_code": "active_incident_event_or_first_nonexpired_event_stops_prefix",
    })

    dedup_days = settings.days["traffic_deduplication"]
    dedup_cutoff = _cutoff(now, dedup_days)
    old_batches, eligible_batches, oldest_batch, newest_batch = _ingest_batch_summary(
        connection, dedup_cutoff
    )
    old_samples = int(connection.execute(
        "SELECT COUNT(*) FROM v3_traffic_ingest_samples s "
        "JOIN v3_traffic_ingest_batches b USING(source_id,source_session_id,batch_id) "
        "WHERE julianday(b.received_at) < julianday(?)", (dedup_cutoff,),
    ).fetchone()[0])
    sample_count = int(connection.execute(
        "SELECT COUNT(*) FROM v3_traffic_ingest_samples s "
        "JOIN v3_traffic_ingest_batches b USING(source_id,source_session_id,batch_id) "
        "WHERE julianday(b.received_at) < julianday(?) AND EXISTS ("
        "SELECT 1 FROM v3_traffic_ingest_batches newest "
        "WHERE newest.source_id=b.source_id AND newest.source_session_id=b.source_session_id "
        "AND newest.batch_sequence > b.batch_sequence)", (dedup_cutoff,),
    ).fetchone()[0])
    sample_bounds = connection.execute(
        "SELECT MIN(b.received_at),MAX(b.received_at) "
        "FROM v3_traffic_ingest_samples s "
        "JOIN v3_traffic_ingest_batches b USING(source_id,source_session_id,batch_id) "
        "WHERE julianday(b.received_at) < julianday(?) AND EXISTS ("
        "SELECT 1 FROM v3_traffic_ingest_batches current "
        "WHERE current.source_id=b.source_id "
        "AND current.source_session_id=b.source_session_id "
        "AND current.batch_sequence > b.batch_sequence)", (dedup_cutoff,),
    ).fetchone()
    table_rows.append({
        "policy": "traffic_deduplication",
        "table": "v3_traffic_ingest_batches",
        "retention_days": dedup_days,
        "cutoff_at": dedup_cutoff,
        "eligible_rows": eligible_batches,
        "planned_rows": min(eligible_batches, batch_limit),
        "protected_rows": max(0, old_batches - eligible_batches),
        "oldest_eligible_at": oldest_batch,
        "newest_eligible_at": newest_batch,
        "selection_reason_code": "expired_batch_ledger_without_child_samples",
        "protection_reason_code": "latest_batch_sequence_per_source_session",
    })
    table_rows.append({
        "policy": "traffic_deduplication",
        "table": "v3_traffic_ingest_samples",
        "retention_days": dedup_days,
        "cutoff_at": dedup_cutoff,
        "eligible_rows": sample_count,
        "planned_rows": min(sample_count, batch_limit),
        "protected_rows": max(0, old_samples - sample_count),
        "oldest_eligible_at": sample_bounds[0],
        "newest_eligible_at": sample_bounds[1],
        "selection_reason_code": "expired_noncurrent_batch_samples",
        "protection_reason_code": "latest_batch_sequence_per_source_session",
    })

    # Refresh history rows are eligible only when their parent session is
    # expired or revoked beyond its own retention window and has no business
    # record. Current sessions and the current token generation stay intact.
    session_cutoff = _cutoff(now, settings.days["mobile_sessions"])
    history_cutoff = _cutoff(now, settings.days["mobile_refresh_history"])
    history_condition = (
        "julianday(h.rotated_at) < julianday(?) AND EXISTS ("
        "SELECT 1 FROM v3_mobile_sessions s WHERE s.session_id=h.session_id "
        "AND julianday(s.refresh_expires_at) < julianday(?) "
        "AND (s.revoked_at IS NULL OR julianday(s.revoked_at) < julianday(?)) "
        "AND NOT EXISTS (SELECT 1 FROM v3_help_requests hr "
        "WHERE hr.mobile_session_id=s.session_id))"
    )
    history_count = int(connection.execute(
        "SELECT COUNT(*) FROM v3_mobile_refresh_history h WHERE " + history_condition,
        (history_cutoff, session_cutoff, session_cutoff),
    ).fetchone()[0])
    old_history_count = int(connection.execute(
        "SELECT COUNT(*) FROM v3_mobile_refresh_history h "
        "WHERE julianday(h.rotated_at) < julianday(?)", (history_cutoff,),
    ).fetchone()[0])
    history_bounds = connection.execute(
        "SELECT MIN(h.rotated_at),MAX(h.rotated_at) "
        "FROM v3_mobile_refresh_history h WHERE " + history_condition,
        (history_cutoff, session_cutoff, session_cutoff),
    ).fetchone()
    table_rows.append({
        "policy": "mobile_refresh_history",
        "table": "v3_mobile_refresh_history",
        "retention_days": settings.days["mobile_refresh_history"],
        "cutoff_at": history_cutoff,
        "eligible_rows": history_count,
        "planned_rows": min(history_count, batch_limit),
        "protected_rows": max(0, old_history_count - history_count),
        "oldest_eligible_at": history_bounds[0],
        "newest_eligible_at": history_bounds[1],
        "selection_reason_code": "expired_refresh_history_for_expired_session",
        "protection_reason_code": "valid_session_or_help_request_reference_or_unexpired_history",
    })
    for row in table_rows:
        policy = row["policy"]
        row["default_retention_days"] = RETENTION_DEFAULTS[policy]
        row["minimum_retention_days"] = MIN_RETENTION_DAYS
        row["maximum_retention_days"] = MAX_RETENTION_DAYS
    return {
        "operation": "plan",
        "observed_at": now_text,
        "schema_version": 9,
        "integrity_ok": True,
        "batch_limit_per_table": batch_limit,
        "policies": table_rows,
        "automatic_maintenance": False,
    }


def plan_database(
    database: str | Path,
    *,
    now: datetime | None = None,
    batch_limit: int = DEFAULT_BATCH_LIMIT,
    settings: RetentionSettings | None = None,
) -> dict:
    """Return a strictly read-only retention plan for an existing v9 database."""
    if type(batch_limit) is not int or not 1 <= batch_limit <= MAX_BATCH_LIMIT:
        raise MaintenanceError("batch_limit_out_of_range", "batch limit is outside the supported range")
    path = _resolve_existing(database)
    current = _utc(now)
    configured = settings or RetentionSettings.from_environment()
    connection = None
    try:
        _check_sidecars(path)
        if _header_journal_mode(path) != "rollback":
            raise InputPathError(
                "database_wal_mode_unsupported",
                "WAL mode requires a quiesced, rollback-journal copy before maintenance",
            )
        before = _file_fingerprint(path)
        connection = _open_readonly(path, immutable=True, strict=False)
        connection.execute("BEGIN")
        version = _validate_schema(connection)
        if not _integrity_ok(connection):
            raise MaintenanceError("database_integrity_check_failed", "database integrity check failed")
        report = _build_plan(connection, current, configured, batch_limit)
        connection.commit()
        report["schema_version"] = version
        report["integrity_ok"] = True
        connection.close()
        connection = None
        sidecars_after = any(
            Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")
        )
        after = _file_fingerprint(path)
        if sidecars_after or after != before:
            raise InputPathError(
                "database_changed_during_plan",
                "database changed while read-only plan was running; discard the plan",
            )
        return report
    except MaintenanceError:
        raise
    except sqlite3.Error as exc:
        raise MaintenanceError(_sqlite_error_code(exc), "retention plan could not be completed") from exc
    except OSError as exc:
        raise MaintenanceError("database_io_error", "retention plan could not be completed") from exc
    finally:
        if connection is not None:
            connection.close()


def _backup_name(now: datetime, directory: Path) -> Path:
    base = "iot-ids-maintenance-" + now.strftime("%Y%m%dT%H%M%SZ")
    for suffix in range(1000):
        tail = "" if suffix == 0 else f"-{suffix:03d}"
        candidate = directory / f"{base}{tail}.sqlite"
        if not candidate.exists():
            return candidate
    raise MaintenanceError("backup_name_exhausted", "cannot allocate a new backup name")


def create_verified_backup(
    connection: sqlite3.Connection, backup_path: Path
) -> dict:
    """Create and verify an atomic SQLite backup using SQLite's backup API."""
    partial_path = backup_path.with_suffix(backup_path.suffix + ".partial")
    if backup_path.exists() or partial_path.exists():
        raise MaintenanceError("backup_path_exists", "refusing to overwrite a backup file")
    target = None
    try:
        if not _integrity_ok(connection):
            raise MaintenanceError("source_integrity_check_failed", "source integrity check failed")
        target = sqlite3.connect(str(partial_path), timeout=0.0)
        connection.backup(target)
        target.commit()
        if not _integrity_ok(target):
            raise MaintenanceError("backup_integrity_check_failed", "backup integrity check failed")
        target.close()
        target = None
        partial_path.replace(backup_path)
        return {"method": "sqlite_backup_api", "integrity_ok": True}
    except MaintenanceError:
        raise
    except sqlite3.Error as exc:
        raise MaintenanceError(_sqlite_error_code(exc), "consistent backup could not be created") from exc
    except OSError as exc:
        raise MaintenanceError("backup_filesystem_failed", "consistent backup could not be created") from exc
    finally:
        if target is not None:
            target.close()
        if partial_path.exists():
            try:
                partial_path.unlink()
            except OSError:
                pass


def _delete_rule_batch(
    connection: sqlite3.Connection,
    rule: RetentionRule,
    cutoff: str,
    now: str,
    batch_limit: int,
) -> int:
    params = _rule_parameters(rule, cutoff, now)
    selected = [row[0] for row in connection.execute(
        f"SELECT rowid FROM {rule.table} WHERE julianday({rule.time_column}) < julianday(?) "
        f"AND ({rule.protection_sql}) ORDER BY rowid LIMIT ?",
        (*params, batch_limit),
    )]
    if not selected:
        return 0
    placeholders = ",".join("?" for _ in selected)
    before = connection.total_changes
    connection.execute(
        f"DELETE FROM {rule.table} WHERE rowid IN ({placeholders})", selected
    )
    return connection.total_changes - before


def _delete_notice_change_prefix(
    connection: sqlite3.Connection, cutoff: str, batch_limit: int
) -> int:
    ids, _old_count, _oldest, _newest = _notice_change_prefix(
        connection, cutoff, batch_limit
    )
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    before = connection.total_changes
    connection.execute(
        f"DELETE FROM v3_mobile_notice_changes WHERE change_id IN ({placeholders})",
        ids,
    )
    return connection.total_changes - before


def _delete_event_prefix(
    connection: sqlite3.Connection,
    cutoff: str,
    active_incidents: set[str],
    batch_limit: int,
) -> int:
    ids = _event_candidates(connection, cutoff, active_incidents, batch_limit)
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    before = connection.total_changes
    connection.execute(
        f"DELETE FROM v3_realtime_events WHERE event_id IN ({placeholders})", ids
    )
    return connection.total_changes - before


def _delete_ingest_batch(
    connection: sqlite3.Connection, cutoff: str, batch_limit: int
) -> tuple[int, int]:
    eligible = (
        "julianday(b.received_at) < julianday(?) AND EXISTS ("
        "SELECT 1 FROM v3_traffic_ingest_batches newest "
        "WHERE newest.source_id=b.source_id "
        "AND newest.source_session_id=b.source_session_id "
        "AND newest.batch_sequence > b.batch_sequence)"
    )
    sample_ids = [
        row[0] for row in connection.execute(
            "SELECT s.rowid FROM v3_traffic_ingest_samples s "
            "JOIN v3_traffic_ingest_batches b USING(source_id,source_session_id,batch_id) "
            f"WHERE {eligible} ORDER BY s.rowid LIMIT ?",
            (cutoff, batch_limit),
        )
    ]
    if sample_ids:
        placeholders = ",".join("?" for _ in sample_ids)
        connection.execute(
            f"DELETE FROM v3_traffic_ingest_samples WHERE rowid IN ({placeholders})",
            sample_ids,
        )
    batch_ids = [
        (row["source_id"], row["source_session_id"], row["batch_id"])
        for row in connection.execute(
            "SELECT b.source_id,b.source_session_id,b.batch_id "
            "FROM v3_traffic_ingest_batches b "
            f"WHERE {eligible} AND NOT EXISTS ("
            "SELECT 1 FROM v3_traffic_ingest_samples s "
            "WHERE s.source_id=b.source_id AND s.source_session_id=b.source_session_id "
            "AND s.batch_id=b.batch_id) ORDER BY b.received_at,b.batch_id LIMIT ?",
            (cutoff, batch_limit),
        )
    ]
    if batch_ids:
        connection.executemany(
            "DELETE FROM v3_traffic_ingest_batches "
            "WHERE source_id=? AND source_session_id=? AND batch_id=?",
            batch_ids,
        )
    return len(batch_ids), len(sample_ids)


def _delete_refresh_history(
    connection: sqlite3.Connection,
    now: datetime,
    settings: RetentionSettings,
    batch_limit: int,
) -> int:
    session_cutoff = _cutoff(now, settings.days["mobile_sessions"])
    history_cutoff = _cutoff(now, settings.days["mobile_refresh_history"])
    selected = connection.execute(
        "SELECT h.refresh_token_selector FROM v3_mobile_refresh_history h "
        "WHERE julianday(h.rotated_at) < julianday(?) AND EXISTS ("
        "SELECT 1 FROM v3_mobile_sessions s WHERE s.session_id=h.session_id "
        "AND julianday(s.refresh_expires_at) < julianday(?) "
        "AND (s.revoked_at IS NULL OR julianday(s.revoked_at) < julianday(?)) "
        "AND NOT EXISTS (SELECT 1 FROM v3_help_requests hr "
        "WHERE hr.mobile_session_id=s.session_id)) "
        "ORDER BY h.rotated_at,h.refresh_token_selector LIMIT ?",
        (history_cutoff, session_cutoff, session_cutoff, batch_limit),
    ).fetchall()
    if not selected:
        return 0
    connection.executemany(
        "DELETE FROM v3_mobile_refresh_history WHERE refresh_token_selector=?",
        [(row[0],) for row in selected],
    )
    return len(selected)


def apply_retention(
    database: str | Path,
    backup_directory: str | Path,
    *,
    now: datetime | None = None,
    batch_limit: int = DEFAULT_BATCH_LIMIT,
    settings: RetentionSettings | None = None,
    before_commit: Callable[[sqlite3.Connection], None] | None = None,
) -> dict:
    """Back up and transactionally delete at most ``batch_limit`` rows per table."""
    if type(batch_limit) is not int or not 1 <= batch_limit <= MAX_BATCH_LIMIT:
        raise MaintenanceError("batch_limit_out_of_range", "batch limit is outside the supported range")
    path = _resolve_existing(database)
    directory = Path(backup_directory).expanduser().resolve()
    if not directory.is_dir():
        raise InputPathError("backup_directory_required", "backup directory must already exist")
    _check_apply_sidecars(path)
    if _header_journal_mode(path) != "rollback":
        raise InputPathError(
            "database_wal_mode_unsupported",
            "WAL mode requires a quiesced, rollback-journal copy before maintenance",
        )
    if not os.access(path, os.W_OK):
        raise MaintenanceError("database_read_only", "database file is read-only")
    current = _utc(now)
    configured = settings or RetentionSettings.from_environment()
    backup_path = _backup_name(current, directory)
    connection = None
    backup_created = False
    try:
        connection = sqlite3.connect(
            _sqlite_uri(path, "rw"), uri=True, timeout=0.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=0")
        mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        wal_present = Path(str(path) + "-wal").exists()
        shm_present = Path(str(path) + "-shm").exists()
        if (wal_present or shm_present) and mode != "wal":
            raise InputPathError(
                "database_sidecar_mode_mismatch",
                "SQLite sidecars do not match the database journal mode",
            )
        connection.execute("BEGIN IMMEDIATE")
        _validate_schema(connection)
        if not _integrity_ok(connection):
            raise MaintenanceError("database_integrity_check_failed", "source integrity check failed")
        pre_plan = _build_plan(connection, current, configured, batch_limit)

        backup_source = _open_readonly(path, strict=False)
        try:
            backup_result = create_verified_backup(backup_source, backup_path)
        finally:
            backup_source.close()
        backup_created = True

        removed: dict[str, int] = {}
        now_text = _iso(current)
        active_incidents = _active_incident_ids(connection)
        removed["v3_realtime_events"] = _delete_event_prefix(
            connection, _cutoff(current, configured.days["realtime_events"]),
            active_incidents, batch_limit,
        )
        removed["v3_mobile_notice_changes"] = _delete_notice_change_prefix(
            connection, _cutoff(current, configured.days["mobile_notice_changes"]),
            batch_limit,
        )
        for rule in RULES:
            if rule.key in {"mobile_refresh_history", "mobile_sessions"}:
                continue
            removed[rule.table] = removed.get(rule.table, 0) + _delete_rule_batch(
                connection, rule, _cutoff(current, configured.days[rule.key]),
                now_text, batch_limit,
            )
        batches, samples = _delete_ingest_batch(
            connection, _cutoff(current, configured.days["traffic_deduplication"]),
            batch_limit,
        )
        removed["v3_traffic_ingest_batches"] = batches
        removed["v3_traffic_ingest_samples"] = samples
        removed["v3_mobile_refresh_history"] = _delete_refresh_history(
            connection, current, configured, batch_limit
        )
        session_rule = next(rule for rule in RULES if rule.key == "mobile_sessions")
        removed["v3_mobile_sessions"] = _delete_rule_batch(
            connection, session_rule,
            _cutoff(current, configured.days["mobile_sessions"]),
            now_text, batch_limit,
        )

        connection.execute(
            "INSERT INTO v3_system_component_health "
            "(component_id,readiness,started_at,ready_at,reason,state_version,updated_at) "
            "VALUES ('database_maintenance','ready',?,?,?,1,?) "
            "ON CONFLICT(component_id) DO UPDATE SET readiness='ready',"
            "started_at=excluded.started_at,ready_at=excluded.ready_at,"
            "reason=excluded.reason,state_version=v3_system_component_health.state_version+1,"
            "updated_at=excluded.updated_at",
            (now_text, now_text, "maintenance_applied", now_text),
        )
        if before_commit is not None:
            before_commit(connection)
        if not _integrity_ok(connection):
            raise MaintenanceError(
                "database_integrity_check_failed",
                "modified database integrity check failed before commit",
            )
        connection.commit()
        return {
            "operation": "apply",
            "observed_at": now_text,
            "backup": {"created": True, **backup_result},
            "pre_integrity_ok": True,
            "post_integrity_ok": True,
            "removed_rows": dict(sorted(removed.items())),
            "retention_policies": len(configured.days),
            "batch_limit_per_table": batch_limit,
            "plan": pre_plan,
        }
    except MaintenanceError:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.Error as exc:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise MaintenanceError(
            _sqlite_error_code(exc), "maintenance transaction failed and was rolled back"
        ) from exc
    except Exception as exc:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise MaintenanceError(
            "maintenance_transaction_failed",
            "maintenance transaction failed and was rolled back",
        ) from exc
    finally:
        if connection is not None:
            connection.close()
        if not backup_created and backup_path.exists():
            try:
                backup_path.unlink()
            except OSError:
                pass


def _parse_now(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        value = datetime.fromisoformat(normalized)
        return _utc(value)
    except (TypeError, ValueError, MaintenanceError) as exc:
        raise MaintenanceError("now_invalid", "--now must be an ISO-8601 timestamp with timezone") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or explicitly apply safe IoT IDS SQLite retention.")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for name in ("plan", "apply"):
        command = subparsers.add_parser(name)
        command.add_argument("--database", required=True)
        command.add_argument("--now", help="ISO-8601 timestamp with timezone")
        command.add_argument("--batch-limit", type=int, default=DEFAULT_BATCH_LIMIT)
        command.add_argument("--json", action="store_true", help="emit stable JSON")
        if name == "apply":
            command.add_argument("--backup-directory", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.database:
            raise InputPathError("database_path_not_configured", "--database is required")
        current = _parse_now(args.now)
        if args.operation == "plan":
            report = plan_database(
                args.database, now=current, batch_limit=args.batch_limit
            )
        else:
            report = apply_retention(
                args.database, args.backup_directory,
                now=current, batch_limit=args.batch_limit,
            )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        else:
            print(f"operation={report['operation']} observed_at={report['observed_at']}")
            if args.operation == "plan":
                for row in report["policies"]:
                    print(
                        f"{row['policy']} table={row['table']} eligible={row['eligible_rows']} "
                        f"planned={row['planned_rows']} protected={row['protected_rows']} "
                        f"cutoff={row['cutoff_at']}"
                    )
            else:
                print("backup_created=1 reason_code=backup_verified")
                for table, count in report["removed_rows"].items():
                    print(f"removed {table}: {count}")
        return EXIT_OK
    except RetentionConfigurationError as exc:
        code = EXIT_INPUT
    except InputPathError as exc:
        code = EXIT_INPUT
    except MaintenanceError as exc:
        code = EXIT_DATABASE if exc.code.startswith(("database_", "schema_")) else EXIT_MAINTENANCE
    except OSError:
        exc = MaintenanceError("filesystem_operation_failed", "maintenance filesystem operation failed")
        code = EXIT_MAINTENANCE
    error = {"error": {"code": exc.code}}
    if getattr(args, "json", False):
        print(json.dumps(error, ensure_ascii=False, sort_keys=True, separators=(",", ":")), file=sys.stderr)
    else:
        print(f"maintenance failed: {exc.code}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
