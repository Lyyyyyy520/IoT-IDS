"""Transactional v3 device profile and lifecycle management."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable, Iterator

from contracts import (
    ConnectionStatus,
    DeviceImportance,
    DeviceProfileSource,
    OperationMode,
    Role,
    enum_values,
    is_valid_device_id,
)
from services.realtime_events import V3DatabaseUnavailable, append_realtime_event
from v3_database import V3_DEVICE_LIFECYCLE_MIGRATION, connect_v3_existing


Clock = Callable[[], datetime]
_MAC_COMPACT_PATTERN = re.compile(r"^[0-9A-Fa-f]{12}$")
_MAC_SEPARATED_PATTERN = re.compile(
    r"^[0-9A-Fa-f]{2}(?P<separator>[:-])"
    r"[0-9A-Fa-f]{2}(?P=separator)[0-9A-Fa-f]{2}(?P=separator)"
    r"[0-9A-Fa-f]{2}(?P=separator)[0-9A-Fa-f]{2}(?P=separator)[0-9A-Fa-f]{2}$"
)
_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_AREA_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_CREATE_PROFILE_SOURCES = {
    DeviceProfileSource.PHYSICAL.value,
    DeviceProfileSource.VIRTUAL.value,
    DeviceProfileSource.GATEWAY.value,
}
_MANAGEMENT_EVENT_TYPE = "device.inventory_changed"
_EXPLICIT_REFERENCE_TABLES = {
    "v3_device_state_observations",
    "v3_mqtt_boot_sessions",
    "v3_mqtt_device_cursors",
    "v3_realtime_events",
    "v3_device_ip_bindings",
    "v3_device_traffic_minutes",
    "v3_device_traffic_protocol_minutes",
    "v3_device_traffic_peer_minutes",
    "v3_incident_devices",
    "v3_help_requests",
}
_REFERENCE_SCAN_EXCLUSIONS = {
    "v3_device_profiles",
    "v3_device_current_state",
    "v3_device_management_audit",
    *_EXPLICIT_REFERENCE_TABLES,
}


class DeviceManagementError(ValueError):
    code = "invalid_device_request"


class DeviceNotFoundError(DeviceManagementError):
    code = "device_not_found"


class DeviceIdConflictError(DeviceManagementError):
    code = "device_id_conflict"


class DeviceIdentityConflictError(DeviceManagementError):
    code = "device_identity_conflict"


class ProfileVersionConflictError(DeviceManagementError):
    code = "profile_version_conflict"


class DeviceHasHistoryError(DeviceManagementError):
    code = "device_has_history"

    def __init__(self, message: str, references: dict):
        super().__init__(message)
        self.references = references


class ConfirmationMismatchError(DeviceManagementError):
    code = "confirmation_mismatch"


class DeviceLifecycleConflictError(DeviceManagementError):
    code = "device_lifecycle_conflict"


@dataclass(frozen=True)
class DeviceActor:
    user_id: int
    username: str
    role: str

    def validate(self) -> None:
        if type(self.user_id) is not int or self.user_id <= 0:
            raise DeviceManagementError("actor user_id must be a positive integer")
        if not self.username or len(self.username) > 128:
            raise DeviceManagementError("actor username is invalid")
        if self.role not in enum_values(Role):
            raise DeviceManagementError("actor role is invalid")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DeviceManagementError("timestamps and injected clocks must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _normalize_mac(value: str) -> str:
    if not isinstance(value, str):
        raise DeviceManagementError("mac must be a string")
    raw = value.strip()
    if _MAC_COMPACT_PATTERN.fullmatch(raw):
        compact = raw.upper()
    elif _MAC_SEPARATED_PATTERN.fullmatch(raw):
        compact = raw.replace(":", "").replace("-", "").upper()
    else:
        raise DeviceManagementError("mac must be a valid 48-bit address")
    return ":".join(compact[index:index + 2] for index in range(0, 12, 2))


def _text(value, field: str, *, minimum: int = 1, maximum: int) -> str:
    if not isinstance(value, str):
        raise DeviceManagementError(f"{field} must be a string")
    normalized = value.strip()
    if len(normalized) < minimum or len(normalized) > maximum:
        raise DeviceManagementError(
            f"{field} length must be between {minimum} and {maximum}"
        )
    if any(ord(character) < 32 for character in normalized):
        raise DeviceManagementError(f"{field} contains control characters")
    return normalized


def _device_type(value) -> str:
    normalized = _text(value, "device_type", maximum=64).lower()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise DeviceManagementError("device_type must be a lowercase identifier")
    return normalized


def _area_id(value) -> str | None:
    if value is None:
        return None
    normalized = _text(value, "area_id", maximum=64)
    if not _AREA_PATTERN.fullmatch(normalized):
        raise DeviceManagementError("area_id contains unsupported characters")
    return normalized


def _enum(value, field: str, allowed) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise DeviceManagementError(f"{field} value is not supported")
    return value


def _version(value) -> int:
    if type(value) is not int or value <= 0:
        raise DeviceManagementError("expected_profile_version must be a positive integer")
    return value


def _device_id(value) -> str:
    if not isinstance(value, str) or not is_valid_device_id(value):
        raise DeviceManagementError("device_id is invalid")
    return value


def _json(value: dict | None) -> str | None:
    if value is None:
        return None
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def ensure_device_lifecycle_schema(connection: sqlite3.Connection) -> None:
    """Verify migration v4 without creating or repairing database objects."""
    try:
        migration = connection.execute(
            "SELECT name, checksum FROM v3_schema_migrations WHERE version = 4"
        ).fetchone()
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'v3_device_management_audit'"
        ).fetchone()
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(v3_device_profiles)")
        }
    except sqlite3.Error as exc:
        raise V3DatabaseUnavailable("v3 lifecycle schema is unavailable") from exc
    if not migration or (
        migration["name"] != V3_DEVICE_LIFECYCLE_MIGRATION.name
        or migration["checksum"] != V3_DEVICE_LIFECYCLE_MIGRATION.checksum
    ):
        raise V3DatabaseUnavailable("v3 lifecycle migration has not been applied")
    required = {
        "importance",
        "profile_source",
        "profile_version",
        "retired_at",
        "retirement_reason",
    }
    if not table or not required <= columns:
        raise V3DatabaseUnavailable("v3 lifecycle schema is incomplete")


class DeviceManagementService:
    """Manage inventory through one explicit, already-migrated SQLite file."""

    def __init__(self, database_path: str | Path | None, *, clock: Clock | None = None):
        self.database_path = Path(database_path) if database_path else None
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        return _utc(self._clock())

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        if self.database_path is None:
            raise V3DatabaseUnavailable("v3 database path is not configured")
        connection = None
        try:
            connection = connect_v3_existing(self.database_path)
            ensure_device_lifecycle_schema(connection)
            if write:
                connection.execute("BEGIN IMMEDIATE")
        except V3DatabaseUnavailable:
            if connection is not None:
                connection.close()
            raise
        except (FileNotFoundError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            raise V3DatabaseUnavailable("v3 lifecycle database is unavailable") from exc
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _profile_snapshot(row: sqlite3.Row) -> dict:
        return {
            "device_id": row["device_id"],
            "mac_address": row["identity_value"],
            "display_name": row["display_name"],
            "device_type": row["device_type"],
            "area_id": row["area_id"],
            "importance": row["importance"],
            "profile_source": row["profile_source"],
            "profile_version": int(row["profile_version"]),
            "operation_mode": row["operation_mode"],
            "retired_at": row["retired_at"],
            "retirement_reason": row["retirement_reason"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _profile_row(connection: sqlite3.Connection, device_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM v3_device_profiles WHERE device_id = ?", (device_id,)
        ).fetchone()
        if not row:
            raise DeviceNotFoundError(f"unknown device_id {device_id!r}")
        return row

    @staticmethod
    def _validate_write_context(actor: DeviceActor, request_id: str) -> str:
        actor.validate()
        if actor.role != Role.ADMIN.value:
            raise DeviceManagementError("device mutations require an admin actor")
        return _text(request_id, "request_id", maximum=128)

    def _record_change(
        self,
        connection: sqlite3.Connection,
        *,
        device_id: str,
        action: str,
        actor: DeviceActor,
        request_id: str,
        occurred_at: datetime,
        before: dict | None,
        after: dict | None,
        profile_version: int,
    ) -> None:
        occurred_text = _iso(occurred_at)
        connection.execute(
            "INSERT INTO v3_device_management_audit "
            "(device_id, action, actor_user_id, actor_username, actor_role, "
            "occurred_at, request_id, before_json, after_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                device_id,
                action,
                actor.user_id,
                actor.username,
                actor.role,
                occurred_text,
                request_id,
                _json(before),
                _json(after),
            ),
        )
        append_realtime_event(
            connection,
            event_type=_MANAGEMENT_EVENT_TYPE,
            occurred_at=occurred_at,
            device_id=None,
            state_version=profile_version,
            payload={
                "action": action,
                "device_id": device_id,
                "profile_version": profile_version,
            },
        )

    def create_device(
        self,
        *,
        device_id: str,
        mac: str,
        display_name: str,
        device_type: str,
        profile_source: str,
        actor: DeviceActor,
        request_id: str,
        area_id: str | None = None,
        importance: str = DeviceImportance.NORMAL.value,
    ) -> dict:
        with self._connection(write=True) as connection:
            return self.create_device_in_transaction(
                connection,
                device_id=device_id,
                mac=mac,
                display_name=display_name,
                device_type=device_type,
                profile_source=profile_source,
                actor=actor,
                request_id=request_id,
                area_id=area_id,
                importance=importance,
            )

    def create_device_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        device_id: str,
        mac: str,
        display_name: str,
        device_type: str,
        profile_source: str,
        actor: DeviceActor,
        request_id: str,
        area_id: str | None = None,
        importance: str = DeviceImportance.NORMAL.value,
    ) -> dict:
        """Create a trusted device inside a caller-owned transaction.

        The helper deliberately leaves commit/rollback to its caller so compound
        operations such as an administrator claiming a discovery candidate can
        atomically create state, audit and realtime inventory records.
        """
        request_id = self._validate_write_context(actor, request_id)
        device_id = _device_id(device_id)
        normalized_mac = _normalize_mac(mac)
        normalized_name = _text(display_name, "display_name", maximum=100)
        normalized_type = _device_type(device_type)
        normalized_area = _area_id(area_id)
        normalized_importance = _enum(
            importance, "importance", enum_values(DeviceImportance)
        )
        normalized_source = _enum(
            profile_source, "profile_source", _CREATE_PROFILE_SOURCES
        )
        occurred_at = self._now()
        now = _iso(occurred_at)
        if connection.execute(
            "SELECT 1 FROM v3_device_profiles WHERE device_id = ?", (device_id,)
        ).fetchone():
            raise DeviceIdConflictError("device_id is already registered")
        if connection.execute(
            "SELECT 1 FROM v3_device_profiles "
            "WHERE identity_kind = 'mac' AND identity_value = ?",
            (normalized_mac,),
        ).fetchone():
            raise DeviceIdentityConflictError("MAC identity is already registered")
        try:
            connection.execute(
                "INSERT INTO v3_device_profiles "
                "(device_id, identity_kind, identity_value, display_name, "
                "device_type, area_id, operation_mode, created_at, updated_at, "
                "importance, profile_source, profile_version, retired_at, "
                "retirement_reason) VALUES (?, 'mac', ?, ?, ?, ?, 'active', "
                "?, ?, ?, ?, 1, NULL, NULL)",
                (
                    device_id,
                    normalized_mac,
                    normalized_name,
                    normalized_type,
                    normalized_area,
                    now,
                    now,
                    normalized_importance,
                    normalized_source,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "device_id" in str(exc):
                raise DeviceIdConflictError("device_id is already registered") from exc
            raise DeviceIdentityConflictError("MAC identity is already registered") from exc
        connection.execute(
            "INSERT INTO v3_device_current_state "
            "(device_id, connection_status, state_version, updated_at) "
            "VALUES (?, 'unknown', 0, ?)",
            (device_id, now),
        )
        after = self._profile_snapshot(self._profile_row(connection, device_id))
        self._record_change(
            connection,
            device_id=device_id,
            action="created",
            actor=actor,
            request_id=request_id,
            occurred_at=occurred_at,
            before=None,
            after=after,
            profile_version=1,
        )
        return self._detail(connection, device_id)

    def update_profile(
        self,
        device_id: str,
        *,
        changes: dict,
        expected_profile_version: int,
        actor: DeviceActor,
        request_id: str,
    ) -> dict:
        request_id = self._validate_write_context(actor, request_id)
        device_id = _device_id(device_id)
        expected = _version(expected_profile_version)
        if not changes:
            raise DeviceManagementError("at least one profile field must be supplied")
        allowed = {"display_name", "device_type", "area_id", "importance"}
        if not set(changes) <= allowed:
            raise DeviceManagementError("profile update contains immutable fields")
        normalized = {}
        if "display_name" in changes:
            normalized["display_name"] = _text(
                changes["display_name"], "display_name", maximum=100
            )
        if "device_type" in changes:
            normalized["device_type"] = _device_type(changes["device_type"])
        if "area_id" in changes:
            normalized["area_id"] = _area_id(changes["area_id"])
        if "importance" in changes:
            normalized["importance"] = _enum(
                changes["importance"], "importance", enum_values(DeviceImportance)
            )
        occurred_at = self._now()
        now = _iso(occurred_at)
        with self._connection(write=True) as connection:
            row = self._profile_row(connection, device_id)
            if int(row["profile_version"]) != expected:
                raise ProfileVersionConflictError("device profile version is stale")
            before = self._profile_snapshot(row)
            if all(before[field] == value for field, value in normalized.items()):
                raise DeviceManagementError("profile update has no effective changes")
            assignments = ", ".join(f"{field} = ?" for field in normalized)
            values = [*normalized.values(), now, device_id, expected]
            cursor = connection.execute(
                f"UPDATE v3_device_profiles SET {assignments}, "
                "profile_version = profile_version + 1, updated_at = ? "
                "WHERE device_id = ? AND profile_version = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise ProfileVersionConflictError("device profile version is stale")
            after = self._profile_snapshot(self._profile_row(connection, device_id))
            self._record_change(
                connection,
                device_id=device_id,
                action="updated",
                actor=actor,
                request_id=request_id,
                occurred_at=occurred_at,
                before=before,
                after=after,
                profile_version=after["profile_version"],
            )
            return self._detail(connection, device_id)

    def set_operation_mode(
        self,
        device_id: str,
        *,
        operation_mode: str,
        expected_profile_version: int,
        actor: DeviceActor,
        request_id: str,
    ) -> dict:
        request_id = self._validate_write_context(actor, request_id)
        device_id = _device_id(device_id)
        expected = _version(expected_profile_version)
        mode = _enum(operation_mode, "operation_mode", enum_values(OperationMode))
        occurred_at = self._now()
        now = _iso(occurred_at)
        with self._connection(write=True) as connection:
            row = self._profile_row(connection, device_id)
            if int(row["profile_version"]) != expected:
                raise ProfileVersionConflictError("device profile version is stale")
            if row["retired_at"] is not None:
                raise DeviceLifecycleConflictError(
                    "retired devices cannot change operation mode"
                )
            if row["operation_mode"] == mode:
                raise DeviceManagementError("operation mode is unchanged")
            before = self._profile_snapshot(row)
            cursor = connection.execute(
                "UPDATE v3_device_profiles SET operation_mode = ?, "
                "profile_version = profile_version + 1, updated_at = ? "
                "WHERE device_id = ? AND profile_version = ?",
                (mode, now, device_id, expected),
            )
            if cursor.rowcount != 1:
                raise ProfileVersionConflictError("device profile version is stale")
            after = self._profile_snapshot(self._profile_row(connection, device_id))
            self._record_change(
                connection,
                device_id=device_id,
                action="operation_mode_changed",
                actor=actor,
                request_id=request_id,
                occurred_at=occurred_at,
                before=before,
                after=after,
                profile_version=after["profile_version"],
            )
            return self._detail(connection, device_id)

    def retire_device(
        self,
        device_id: str,
        *,
        reason: str,
        expected_profile_version: int,
        actor: DeviceActor,
        request_id: str,
    ) -> dict:
        request_id = self._validate_write_context(actor, request_id)
        device_id = _device_id(device_id)
        expected = _version(expected_profile_version)
        normalized_reason = _text(reason, "reason", maximum=500)
        occurred_at = self._now()
        now = _iso(occurred_at)
        with self._connection(write=True) as connection:
            row = self._profile_row(connection, device_id)
            if int(row["profile_version"]) != expected:
                raise ProfileVersionConflictError("device profile version is stale")
            if row["retired_at"] is not None:
                raise DeviceLifecycleConflictError("device is already retired")
            before = self._profile_snapshot(row)
            cursor = connection.execute(
                "UPDATE v3_device_profiles SET operation_mode = 'disabled', "
                "retired_at = ?, retirement_reason = ?, "
                "profile_version = profile_version + 1, updated_at = ? "
                "WHERE device_id = ? AND profile_version = ?",
                (now, normalized_reason, now, device_id, expected),
            )
            if cursor.rowcount != 1:
                raise ProfileVersionConflictError("device profile version is stale")
            after = self._profile_snapshot(self._profile_row(connection, device_id))
            self._record_change(
                connection,
                device_id=device_id,
                action="retired",
                actor=actor,
                request_id=request_id,
                occurred_at=occurred_at,
                before=before,
                after=after,
                profile_version=after["profile_version"],
            )
            result = self._detail(connection, device_id)
            result["credential_revocation_required"] = True
            return result

    def restore_device(
        self,
        device_id: str,
        *,
        expected_profile_version: int,
        actor: DeviceActor,
        request_id: str,
    ) -> dict:
        request_id = self._validate_write_context(actor, request_id)
        device_id = _device_id(device_id)
        expected = _version(expected_profile_version)
        occurred_at = self._now()
        now = _iso(occurred_at)
        with self._connection(write=True) as connection:
            row = self._profile_row(connection, device_id)
            if int(row["profile_version"]) != expected:
                raise ProfileVersionConflictError("device profile version is stale")
            if row["retired_at"] is None:
                raise DeviceLifecycleConflictError("device is not retired")
            before = self._profile_snapshot(row)
            cursor = connection.execute(
                "UPDATE v3_device_profiles SET operation_mode = 'active', "
                "retired_at = NULL, retirement_reason = NULL, "
                "profile_version = profile_version + 1, updated_at = ? "
                "WHERE device_id = ? AND profile_version = ?",
                (now, device_id, expected),
            )
            if cursor.rowcount != 1:
                raise ProfileVersionConflictError("device profile version is stale")
            after = self._profile_snapshot(self._profile_row(connection, device_id))
            self._record_change(
                connection,
                device_id=device_id,
                action="restored",
                actor=actor,
                request_id=request_id,
                occurred_at=occurred_at,
                before=before,
                after=after,
                profile_version=after["profile_version"],
            )
            result = self._detail(connection, device_id)
            result["credential_reverification_required"] = True
            return result

    @staticmethod
    def _table_has_device_id(
        connection: sqlite3.Connection, table_name: str
    ) -> bool:
        escaped = table_name.replace('"', '""')
        return any(
            row[1] == "device_id"
            for row in connection.execute(
                f'PRAGMA table_info("{escaped}")'
            ).fetchall()
        )

    def _references(self, connection: sqlite3.Connection, device_id: str) -> dict:
        def count(query: str, parameters=()) -> int:
            return int(connection.execute(query, parameters).fetchone()[0])

        def table_exists(name: str) -> bool:
            return connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone() is not None

        references = {
            "state_observations": count(
                "SELECT COUNT(*) FROM v3_device_state_observations WHERE device_id = ?",
                (device_id,),
            ),
            "mqtt_boot_sessions": count(
                "SELECT COUNT(*) FROM v3_mqtt_boot_sessions WHERE device_id = ?",
                (device_id,),
            ),
            "mqtt_cursor": count(
                "SELECT COUNT(*) FROM v3_mqtt_device_cursors WHERE device_id = ?",
                (device_id,),
            ),
            "non_management_events": count(
                "SELECT COUNT(*) FROM v3_realtime_events "
                "WHERE device_id = ? AND event_type != ?",
                (device_id, _MANAGEMENT_EVENT_TYPE),
            ),
            "management_audits": count(
                "SELECT COUNT(*) FROM v3_device_management_audit WHERE device_id = ?",
                (device_id,),
            ),
            "current_state_placeholder": count(
                "SELECT COUNT(*) FROM v3_device_current_state WHERE device_id = ?",
                (device_id,),
            ),
            "traffic_ip_bindings": count(
                "SELECT COUNT(*) FROM v3_device_ip_bindings WHERE device_id = ?",
                (device_id,),
            ),
            "traffic_minutes": count(
                "SELECT COUNT(*) FROM v3_device_traffic_minutes WHERE device_id = ?",
                (device_id,),
            ),
            "traffic_protocol_minutes": count(
                "SELECT COUNT(*) FROM v3_device_traffic_protocol_minutes WHERE device_id = ?",
                (device_id,),
            ),
            "traffic_peer_minutes": count(
                "SELECT COUNT(*) FROM v3_device_traffic_peer_minutes "
                "WHERE device_id = ? OR peer_device_id = ?",
                (device_id, device_id),
            ),
            "incident_devices": count(
                "SELECT COUNT(*) FROM v3_incident_devices "
                "WHERE device_id = ?",
                (device_id,),
            ) if table_exists("v3_incident_devices") else 0,
            "help_requests": count(
                "SELECT COUNT(*) FROM v3_help_requests "
                "WHERE device_id = ?",
                (device_id,),
            ) if table_exists("v3_help_requests") else 0,
            "discovery_candidates": count(
                "SELECT COUNT(*) FROM v3_discovered_device_candidates "
                "WHERE claimed_device_id = ?",
                (device_id,),
            ) if table_exists("v3_discovered_device_candidates") else 0,
            "future_references": {},
        }
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        for table_name in tables:
            if table_name in _REFERENCE_SCAN_EXCLUSIONS:
                continue
            if not self._table_has_device_id(connection, table_name):
                continue
            escaped = table_name.replace('"', '""')
            table_count = count(
                f'SELECT COUNT(*) FROM "{escaped}" WHERE device_id = ?',
                (device_id,),
            )
            if table_count:
                references["future_references"][table_name] = table_count
        return references

    @staticmethod
    def _blocking_reasons(references: dict) -> list[str]:
        reasons = [
            key
            for key in (
                "state_observations",
                "mqtt_boot_sessions",
                "mqtt_cursor",
                "non_management_events",
                "traffic_ip_bindings",
                "traffic_minutes",
                "traffic_protocol_minutes",
                "traffic_peer_minutes",
                "incident_devices",
                "help_requests",
                "discovery_candidates",
            )
            if references[key] > 0
        ]
        reasons.extend(
            f"future_reference:{name}"
            for name in sorted(references["future_references"])
        )
        return reasons

    def delete_device(
        self,
        device_id: str,
        *,
        confirmation: str,
        actor: DeviceActor,
        request_id: str,
    ) -> dict:
        request_id = self._validate_write_context(actor, request_id)
        device_id = _device_id(device_id)
        if not isinstance(confirmation, str):
            raise ConfirmationMismatchError("display_name confirmation is required")
        occurred_at = self._now()
        with self._connection(write=True) as connection:
            row = self._profile_row(connection, device_id)
            before = self._profile_snapshot(row)
            if confirmation != row["display_name"]:
                raise ConfirmationMismatchError(
                    "display_name confirmation does not match"
                )
            references = self._references(connection, device_id)
            blocking = self._blocking_reasons(references)
            if blocking:
                raise DeviceHasHistoryError(
                    "device has retained historical evidence",
                    {"counts": references, "blocking_reasons": blocking},
                )
            connection.execute(
                "DELETE FROM v3_device_current_state WHERE device_id = ?",
                (device_id,),
            )
            try:
                deleted = connection.execute(
                    "DELETE FROM v3_device_profiles WHERE device_id = ?",
                    (device_id,),
                )
            except sqlite3.IntegrityError as exc:
                raise DeviceHasHistoryError(
                    "device has an unclassified foreign-key reference",
                    {
                        "counts": references,
                        "blocking_reasons": ["foreign_key_reference"],
                    },
                ) from exc
            if deleted.rowcount != 1:
                raise DeviceNotFoundError(f"unknown device_id {device_id!r}")
            self._record_change(
                connection,
                device_id=device_id,
                action="deleted",
                actor=actor,
                request_id=request_id,
                occurred_at=occurred_at,
                before=before,
                after=None,
                profile_version=before["profile_version"] + 1,
            )
            return {
                "device_id": device_id,
                "deleted": True,
                "retained_management_audit": True,
            }

    def _detail(self, connection: sqlite3.Connection, device_id: str) -> dict:
        row = connection.execute(
            "SELECT p.*, c.connection_status, c.ip_address, c.state_version, "
            "c.last_observed_at, c.last_received_at "
            "FROM v3_device_profiles p JOIN v3_device_current_state c "
            "ON c.device_id = p.device_id WHERE p.device_id = ?",
            (device_id,),
        ).fetchone()
        if not row:
            raise DeviceNotFoundError(f"unknown device_id {device_id!r}")
        sources = [
            source[0]
            for source in connection.execute(
                "SELECT DISTINCT source FROM v3_device_state_observations "
                "WHERE device_id = ? ORDER BY source",
                (device_id,),
            ).fetchall()
        ]
        references = self._references(connection, device_id)
        blocking = self._blocking_reasons(references)
        profile = self._profile_snapshot(row)
        return {
            **profile,
            "connection_status": row["connection_status"],
            "ip_address": row["ip_address"],
            "state_version": int(row["state_version"]),
            "observed_at": row["last_observed_at"],
            "received_at": row["last_received_at"],
            "sources": sources,
            "lifecycle_status": "retired" if row["retired_at"] else "active",
            "references": references,
            "can_delete": not blocking,
            "delete_blocking_reasons": blocking,
        }

    def get_device(self, device_id: str) -> dict:
        device_id = _device_id(device_id)
        with self._connection() as connection:
            return self._detail(connection, device_id)

    def list_devices(
        self,
        *,
        search: str | None = None,
        connection_status: str | None = None,
        operation_mode: str | None = None,
        area_id: str | None = None,
        retired: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        if type(limit) is not int or limit < 1 or limit > 100:
            raise DeviceManagementError("limit must be between 1 and 100")
        if type(offset) is not int or offset < 0 or offset > 1_000_000:
            raise DeviceManagementError("offset must be between 0 and 1000000")
        conditions = []
        parameters: list = []
        if search is not None:
            query = _text(search, "search", maximum=100)
            escaped = (
                query.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            conditions.append(
                "(LOWER(p.display_name) LIKE LOWER(?) ESCAPE '\\' "
                "OR LOWER(p.device_id) LIKE LOWER(?) ESCAPE '\\')"
            )
            parameters.extend([f"%{escaped}%", f"%{escaped}%"])
        if connection_status is not None:
            conditions.append("c.connection_status = ?")
            parameters.append(
                _enum(
                    connection_status,
                    "connection_status",
                    enum_values(ConnectionStatus),
                )
            )
        if operation_mode is not None:
            conditions.append("p.operation_mode = ?")
            parameters.append(
                _enum(operation_mode, "operation_mode", enum_values(OperationMode))
            )
        if area_id is not None:
            conditions.append("p.area_id = ?")
            parameters.append(_area_id(area_id))
        if retired is not None:
            conditions.append(
                "p.retired_at IS NOT NULL" if retired else "p.retired_at IS NULL"
            )
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connection() as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM v3_device_profiles p "
                    "JOIN v3_device_current_state c ON c.device_id = p.device_id"
                    + where,
                    parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                "SELECT p.device_id, p.display_name, p.device_type, p.area_id, "
                "p.importance, p.profile_source, p.profile_version, p.operation_mode, "
                "p.retired_at, p.retirement_reason, c.connection_status, "
                "c.ip_address, c.state_version, c.last_received_at "
                "FROM v3_device_profiles p JOIN v3_device_current_state c "
                "ON c.device_id = p.device_id"
                + where
                + " ORDER BY p.device_id LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()
        items = [
            {
                **dict(row),
                "profile_version": int(row["profile_version"]),
                "state_version": int(row["state_version"]),
                "lifecycle_status": "retired" if row["retired_at"] else "active",
            }
            for row in rows
        ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}


__all__ = [
    "ConfirmationMismatchError",
    "DeviceActor",
    "DeviceHasHistoryError",
    "DeviceIdConflictError",
    "DeviceIdentityConflictError",
    "DeviceLifecycleConflictError",
    "DeviceManagementError",
    "DeviceManagementService",
    "DeviceNotFoundError",
    "ProfileVersionConflictError",
    "ensure_device_lifecycle_schema",
]
