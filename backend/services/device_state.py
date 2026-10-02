"""Device identity, connection state, operation mode, and component health."""
from contextlib import contextmanager
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
from typing import Callable, Iterator

from contracts import (
    ConnectionStatus,
    DetectionReadiness,
    OFFLINE_AFTER_SECONDS,
    OperationMode,
    STALE_AFTER_SECONDS,
    enum_values,
    is_valid_device_id,
)
from services.realtime_events import append_realtime_event
from v3_database import connect_v3, connect_v3_existing, initialize_v3_database


Clock = Callable[[], datetime]
_COMPONENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")


class DeviceStateError(ValueError):
    """Base error for invalid device-state operations."""


class DeviceNotFoundError(DeviceStateError):
    """Raised when a stable device id has not been registered."""


class DeviceIdentityConflictError(DeviceStateError):
    """Raised when a device id and immutable hardware identity disagree."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DeviceStateError("timestamps and injected clocks must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _normalize_identity(kind: str, value: str) -> tuple[str, str]:
    normalized_kind = (kind or "").strip().lower()
    normalized_value = (value or "").strip()
    if not normalized_kind or not normalized_value:
        raise DeviceStateError("identity_kind and identity_value are required")
    if normalized_kind == "mac":
        compact = re.sub(r"[^0-9a-fA-F]", "", normalized_value)
        if len(compact) != 12 or not re.fullmatch(r"[0-9a-fA-F]{12}", compact):
            raise DeviceStateError("invalid MAC identity")
        normalized_value = ":".join(
            compact[index:index + 2] for index in range(0, 12, 2)
        ).upper()
    return normalized_kind, normalized_value


def _normalize_ip(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError as exc:
        raise DeviceStateError("invalid IP address") from exc


def _device_sources(connection, device_id: str) -> list[str]:
    return [
        row["source"]
        for row in connection.execute(
            "SELECT DISTINCT source FROM v3_device_state_observations "
            "WHERE device_id = ? ORDER BY source",
            (device_id,),
        ).fetchall()
    ]


class DeviceStateService:
    """SQLite-backed v3 state service with a deterministic injectable clock."""

    def __init__(
        self,
        database_path: str | Path,
        clock: Clock | None = None,
        *,
        create_if_missing: bool = True,
    ):
        self.database_path = Path(database_path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._create_if_missing = create_if_missing

    def initialize(self) -> None:
        initialize_v3_database(self.database_path)

    def _now(self) -> datetime:
        return _utc(self._clock())

    @contextmanager
    def _connection(self) -> Iterator:
        connector = connect_v3 if self._create_if_missing else connect_v3_existing
        connection = connector(self.database_path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def bind_device(
        self,
        *,
        device_id: str,
        identity_kind: str,
        identity_value: str,
        display_name: str,
        device_type: str,
        area_id: str | None = None,
    ) -> dict:
        """Bind one stable device id to one immutable device identity."""
        if not is_valid_device_id(device_id):
            raise DeviceStateError("invalid stable device_id")
        normalized_kind, normalized_value = _normalize_identity(
            identity_kind, identity_value
        )
        if not display_name.strip() or not device_type.strip():
            raise DeviceStateError("display_name and device_type are required")
        now = _iso(self._now())

        with self._connection() as connection:
            by_device = connection.execute(
                "SELECT identity_kind, identity_value FROM v3_device_profiles "
                "WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            by_identity = connection.execute(
                "SELECT device_id FROM v3_device_profiles "
                "WHERE identity_kind = ? AND identity_value = ?",
                (normalized_kind, normalized_value),
            ).fetchone()

            if by_device and (
                by_device["identity_kind"] != normalized_kind
                or by_device["identity_value"] != normalized_value
            ):
                raise DeviceIdentityConflictError(
                    f"device_id {device_id!r} is already bound to another identity"
                )
            if by_identity and by_identity["device_id"] != device_id:
                raise DeviceIdentityConflictError(
                    "device identity is already bound to another device_id"
                )

            if by_device:
                connection.execute(
                    "UPDATE v3_device_profiles "
                    "SET display_name = ?, device_type = ?, area_id = ?, updated_at = ? "
                    "WHERE device_id = ?",
                    (
                        display_name.strip(),
                        device_type.strip(),
                        area_id.strip() if area_id else None,
                        now,
                        device_id,
                    ),
                )
            else:
                connection.execute(
                    "INSERT INTO v3_device_profiles "
                    "(device_id, identity_kind, identity_value, display_name, "
                    "device_type, area_id, operation_mode, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                    (
                        device_id,
                        normalized_kind,
                        normalized_value,
                        display_name.strip(),
                        device_type.strip(),
                        area_id.strip() if area_id else None,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO v3_device_current_state "
                    "(device_id, connection_status, state_version, updated_at) "
                    "VALUES (?, 'unknown', 0, ?)",
                    (device_id, now),
                )

        return self.get_device_state(device_id)

    def record_observation(
        self,
        *,
        device_id: str,
        identity_kind: str,
        identity_value: str,
        source: str,
        ip_address: str | None,
        observed_at: datetime | None = None,
        received_at: datetime | None = None,
        sequence: int | None = None,
        payload: dict | None = None,
        boot_id: str | None = None,
        firmware_version: str | None = None,
        uptime_ms: int | None = None,
    ) -> dict:
        """Append an observation and advance current state without changing identity."""
        received = _utc(received_at) if received_at is not None else self._now()
        with self._connection() as connection:
            self.record_observation_in_transaction(
                connection,
                device_id=device_id,
                identity_kind=identity_kind,
                identity_value=identity_value,
                source=source,
                ip_address=ip_address,
                observed_at=observed_at,
                received_at=received,
                sequence=sequence,
                payload=payload,
                boot_id=boot_id,
                firmware_version=firmware_version,
                uptime_ms=uptime_ms,
            )
        return self.get_device_state(device_id)

    def record_observation_in_transaction(
        self,
        connection,
        *,
        device_id: str,
        identity_kind: str,
        identity_value: str,
        source: str,
        ip_address: str | None,
        received_at: datetime,
        observed_at: datetime | None = None,
        sequence: int | None = None,
        payload: dict | None = None,
        boot_id: str | None = None,
        firmware_version: str | None = None,
        uptime_ms: int | None = None,
    ) -> dict:
        """Write an observation on a caller-owned transaction.

        MQTT ingestion uses this entry so its replay cursor and the device
        observation either commit together or roll back together.
        """
        normalized_kind, normalized_value = _normalize_identity(
            identity_kind, identity_value
        )
        normalized_ip = _normalize_ip(ip_address)
        if not source.strip():
            raise DeviceStateError("observation source is required")
        if sequence is not None and sequence < 0:
            raise DeviceStateError("sequence must not be negative")
        if uptime_ms is not None and uptime_ms < 0:
            raise DeviceStateError("uptime_ms must not be negative")

        received = _utc(received_at)
        observed = _utc(observed_at) if observed_at is not None else received
        received_text = _iso(received)
        observed_text = _iso(observed)
        payload_json = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if payload is not None
            else None
        )

        profile = connection.execute(
            "SELECT identity_kind, identity_value FROM v3_device_profiles "
            "WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if not profile:
            raise DeviceNotFoundError(f"unknown device_id {device_id!r}")
        if (
            profile["identity_kind"] != normalized_kind
            or profile["identity_value"] != normalized_value
        ):
            raise DeviceIdentityConflictError(
                f"observation identity does not match device_id {device_id!r}"
            )

        current = connection.execute(
            "SELECT connection_status, state_version "
            "FROM v3_device_current_state WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if not current:
            raise DeviceNotFoundError(f"device state missing for {device_id!r}")
        previous_status = current["connection_status"]
        next_state_version = int(current["state_version"]) + 1

        cursor = connection.execute(
            "INSERT INTO v3_device_state_observations "
            "(device_id, source, sequence, ip_address, observed_at, "
            "received_at, payload_json, boot_id, firmware_version, uptime_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                device_id,
                source.strip(),
                sequence,
                normalized_ip,
                observed_text,
                received_text,
                payload_json,
                boot_id,
                firmware_version,
                uptime_ms,
            ),
        )
        # The backend receive time is the trusted start of an IP binding. Keep
        # this update in the observation transaction so historical traffic is
        # never interpreted from only the profile's latest IP address.
        if normalized_ip is not None:
            from services.device_traffic import record_ip_binding

            record_ip_binding(
                connection,
                device_id=device_id,
                ip_address=normalized_ip,
                valid_from=received,
                source=source.strip(),
                source_observation_id=int(cursor.lastrowid),
                created_at=received,
            )
        connection.execute(
            "UPDATE v3_device_current_state SET "
            "connection_status = 'online', ip_address = ?, "
            "last_observed_at = ?, last_received_at = ?, "
            "last_observation_id = ?, state_version = state_version + 1, "
            "updated_at = ? WHERE device_id = ?",
            (
                normalized_ip,
                observed_text,
                received_text,
                cursor.lastrowid,
                received_text,
                device_id,
            ),
        )
        if previous_status == ConnectionStatus.ONLINE.value:
            event_type = "device.telemetry_updated"
            event_payload = {
                "connection_status": ConnectionStatus.ONLINE.value,
                "observation_id": int(cursor.lastrowid),
                "source": source.strip(),
                "ip_address": normalized_ip,
                "observed_at": observed_text,
                "received_at": received_text,
                "sources": _device_sources(connection, device_id),
            }
        else:
            event_type = "device.connection_changed"
            event_payload = {
                "from": previous_status,
                "to": ConnectionStatus.ONLINE.value,
                "source": source.strip(),
                "connection_status": ConnectionStatus.ONLINE.value,
                "ip_address": normalized_ip,
                "observed_at": observed_text,
                "received_at": received_text,
                "sources": _device_sources(connection, device_id),
            }
        event = append_realtime_event(
            connection,
            event_type=event_type,
            occurred_at=received,
            device_id=device_id,
            state_version=next_state_version,
            payload=event_payload,
        )
        return {
            "observation_id": cursor.lastrowid,
            "state_version": next_state_version,
            "received_at": received_text,
            "event_id": event["event_id"],
        }

    def _connection_status(self, last_received_at: str | None, now: datetime) -> str:
        if last_received_at is None:
            return ConnectionStatus.UNKNOWN.value
        age_seconds = max(0.0, (now - _parse(last_received_at)).total_seconds())
        if age_seconds < STALE_AFTER_SECONDS:
            return ConnectionStatus.ONLINE.value
        if age_seconds < OFFLINE_AFTER_SECONDS:
            return ConnectionStatus.STALE.value
        return ConnectionStatus.OFFLINE.value

    def _refresh_device(self, connection, device_id: str, now: datetime) -> bool:
        current = connection.execute(
            "SELECT connection_status, ip_address, last_observed_at, "
            "last_received_at, state_version "
            "FROM v3_device_current_state WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if not current:
            raise DeviceNotFoundError(f"unknown device_id {device_id!r}")
        next_status = self._connection_status(current["last_received_at"], now)
        if next_status != current["connection_status"]:
            next_state_version = int(current["state_version"]) + 1
            connection.execute(
                "UPDATE v3_device_current_state SET connection_status = ?, "
                "state_version = state_version + 1, updated_at = ? "
                "WHERE device_id = ?",
                (next_status, _iso(now), device_id),
            )
            append_realtime_event(
                connection,
                event_type="device.connection_changed",
                occurred_at=now,
                device_id=device_id,
                state_version=next_state_version,
                payload={
                    "from": current["connection_status"],
                    "to": next_status,
                    "source": "timeout",
                    "connection_status": next_status,
                    "ip_address": current["ip_address"],
                    "observed_at": current["last_observed_at"],
                    "received_at": current["last_received_at"],
                    "sources": _device_sources(connection, device_id),
                },
            )
            return True
        return False

    def refresh_connection_statuses(self) -> int:
        """Apply timeout transitions to all devices and return the changed count."""
        now = self._now()
        changed = 0
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT device_id, connection_status, ip_address, last_observed_at, "
                "last_received_at, state_version "
                "FROM v3_device_current_state"
            ).fetchall()
            for row in rows:
                next_status = self._connection_status(row["last_received_at"], now)
                if next_status != row["connection_status"]:
                    next_state_version = int(row["state_version"]) + 1
                    connection.execute(
                        "UPDATE v3_device_current_state SET connection_status = ?, "
                        "state_version = state_version + 1, updated_at = ? "
                        "WHERE device_id = ?",
                        (next_status, _iso(now), row["device_id"]),
                    )
                    append_realtime_event(
                        connection,
                        event_type="device.connection_changed",
                        occurred_at=now,
                        device_id=row["device_id"],
                        state_version=next_state_version,
                        payload={
                            "from": row["connection_status"],
                            "to": next_status,
                            "source": "timeout",
                            "connection_status": next_status,
                            "ip_address": row["ip_address"],
                            "observed_at": row["last_observed_at"],
                            "received_at": row["last_received_at"],
                            "sources": _device_sources(connection, row["device_id"]),
                        },
                    )
                    changed += 1
        return changed

    def get_device_state(self, device_id: str) -> dict:
        now = self._now()
        with self._connection() as connection:
            self._refresh_device(connection, device_id, now)
            row = connection.execute(
                "SELECT p.device_id, p.identity_kind, p.identity_value, "
                "p.display_name, p.device_type, p.area_id, p.operation_mode, "
                "c.connection_status, c.ip_address, c.last_observed_at, "
                "c.last_received_at, c.last_observation_id, c.state_version, "
                "c.updated_at FROM v3_device_profiles p "
                "JOIN v3_device_current_state c ON c.device_id = p.device_id "
                "WHERE p.device_id = ?",
                (device_id,),
            ).fetchone()
            if not row:
                raise DeviceNotFoundError(f"unknown device_id {device_id!r}")
            result = dict(row)
        result["offline_alert_eligible"] = (
            result["operation_mode"] == OperationMode.ACTIVE.value
            and result["connection_status"] == ConnectionStatus.OFFLINE.value
        )
        return result

    def set_operation_mode(self, device_id: str, mode: str | OperationMode) -> dict:
        normalized_mode = mode.value if isinstance(mode, OperationMode) else str(mode)
        if normalized_mode not in enum_values(OperationMode):
            raise DeviceStateError("invalid operation mode")
        now = _iso(self._now())
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE v3_device_profiles SET operation_mode = ?, updated_at = ? "
                "WHERE device_id = ?",
                (normalized_mode, now, device_id),
            )
            if cursor.rowcount != 1:
                raise DeviceNotFoundError(f"unknown device_id {device_id!r}")
            connection.execute(
                "UPDATE v3_device_current_state "
                "SET state_version = state_version + 1, updated_at = ? "
                "WHERE device_id = ?",
                (now, device_id),
            )
        return self.get_device_state(device_id)

    def restart_component(self, component_id: str, reason: str | None = None) -> dict:
        """Record a component restart and force readiness back to warming_up."""
        return self._write_component(
            component_id,
            DetectionReadiness.WARMING_UP.value,
            reason,
            restarted=True,
        )

    def set_component_readiness(
        self,
        component_id: str,
        readiness: str | DetectionReadiness,
        reason: str | None = None,
    ) -> dict:
        normalized = readiness.value if isinstance(readiness, DetectionReadiness) else str(readiness)
        if normalized not in enum_values(DetectionReadiness):
            raise DeviceStateError("invalid component readiness")
        return self._write_component(component_id, normalized, reason, restarted=False)

    def _write_component(
        self,
        component_id: str,
        readiness: str,
        reason: str | None,
        *,
        restarted: bool,
    ) -> dict:
        if not _COMPONENT_ID_PATTERN.fullmatch(component_id or ""):
            raise DeviceStateError("invalid component_id")
        current_time = self._now()
        now = _iso(current_time)
        normalized_reason = reason.strip() if reason else None
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM v3_system_component_health WHERE component_id = ?",
                (component_id,),
            ).fetchone()
            if existing:
                readiness_changed = existing["readiness"] != readiness
                reason_changed = existing["reason"] != normalized_reason
                if not restarted and not readiness_changed and not reason_changed:
                    return dict(existing)
                started_at = now if restarted else existing["started_at"]
                if restarted:
                    ready_at = None
                elif readiness == DetectionReadiness.READY.value:
                    ready_at = now
                else:
                    ready_at = existing["ready_at"]
                connection.execute(
                    "UPDATE v3_system_component_health SET readiness = ?, "
                    "started_at = ?, ready_at = ?, reason = ?, "
                    "state_version = state_version + 1, updated_at = ? "
                    "WHERE component_id = ?",
                    (
                        readiness,
                        started_at,
                        ready_at,
                        normalized_reason,
                        now,
                        component_id,
                    ),
                )
                state_version = int(existing["state_version"]) + 1
                previous_readiness = existing["readiness"]
                previous_reason = existing["reason"]
            else:
                ready_at = now if readiness == DetectionReadiness.READY.value else None
                connection.execute(
                    "INSERT INTO v3_system_component_health "
                    "(component_id, readiness, started_at, ready_at, reason, "
                    "state_version, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (
                        component_id,
                        readiness,
                        now,
                        ready_at,
                        normalized_reason,
                        now,
                    ),
                )
                state_version = 1
                readiness_changed = True
                reason_changed = normalized_reason is not None
                previous_readiness = None
                previous_reason = None
            if readiness_changed or reason_changed:
                append_realtime_event(
                    connection,
                    event_type="system.component_changed",
                    occurred_at=current_time,
                    device_id=None,
                    state_version=state_version,
                    payload={
                        "component_id": component_id,
                        "from": previous_readiness,
                        "to": readiness,
                        "previous_reason": previous_reason,
                        "readiness": readiness,
                        "started_at": started_at if existing else now,
                        "ready_at": ready_at,
                        "reason": normalized_reason,
                        "updated_at": now,
                    },
                )
        return self.get_component_health(component_id)

    def get_component_health(self, component_id: str) -> dict:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM v3_system_component_health WHERE component_id = ?",
                (component_id,),
            ).fetchone()
            if not row:
                raise DeviceStateError(f"unknown component_id {component_id!r}")
            return dict(row)
