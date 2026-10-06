"""Pure MQTT heartbeat validation and persistence, without a network client."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import ipaddress
import json
import math
import re
import sqlite3
from typing import Any

from contracts import (
    MQTT_BOOT_ID_HEX_LENGTH,
    MQTT_HEARTBEAT_MAX_BYTES,
    MQTT_HEARTBEAT_SCHEMA_VERSION,
    MQTT_INITIAL_SEQUENCE_VALUES,
    MQTT_TELEMETRY_MAX_BYTES,
    is_valid_device_id,
)
from services.device_state import (
    DeviceIdentityConflictError,
    DeviceNotFoundError,
    DeviceStateError,
    DeviceStateService,
)
from v3_database import connect_v3


_TOPIC_PATTERN = re.compile(r"^community/([^/]+)/status$")
_BOOT_ID_PATTERN = re.compile(
    rf"^[0-9a-f]{{{MQTT_BOOT_ID_HEX_LENGTH}}}$"
)
_FIRMWARE_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$")
_MAX_SQLITE_INTEGER = (1 << 63) - 1
_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "device_id",
        "boot_id",
        "sequence",
        "firmware_version",
        "uptime_ms",
        "ip",
        "mac",
        "telemetry",
    }
)
_OPTIONAL_FIELDS = frozenset({"device_time"})


@dataclass(frozen=True)
class HeartbeatMessage:
    device_id: str
    boot_id: str
    sequence: int
    firmware_version: str
    uptime_ms: int
    ip_address: str
    mac_address: str
    telemetry: dict
    device_time: datetime | None = None


@dataclass(frozen=True)
class IngestionResult:
    accepted: bool
    code: str
    message: str
    device_id: str | None = None
    boot_id: str | None = None
    sequence: int | None = None
    observation_id: int | None = None
    state_version: int | None = None
    candidate_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class HeartbeatValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HeartbeatValidationError(
            "invalid_received_at", "received_at must include a timezone"
        )
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise HeartbeatValidationError(
                "invalid_json", f"duplicate JSON field: {key}"
            )
        result[key] = value
    return result


def _reject_json_constant(value: str):
    raise HeartbeatValidationError(
        "invalid_json", f"non-standard JSON constant is not allowed: {value}"
    )


def _normalize_mac(value: Any) -> str:
    if not isinstance(value, str):
        raise HeartbeatValidationError("invalid_mac", "mac must be a string")
    plain = re.fullmatch(r"[0-9a-fA-F]{12}", value)
    separated = re.fullmatch(
        r"[0-9a-fA-F]{2}([:-])[0-9a-fA-F]{2}"
        r"(?:\1[0-9a-fA-F]{2}){4}",
        value,
    )
    if not plain and not separated:
        raise HeartbeatValidationError("invalid_mac", "mac must contain six octets")
    compact = re.sub(r"[:-]", "", value)
    octets = [int(compact[index:index + 2], 16) for index in range(0, 12, 2)]
    if all(octet == 0 for octet in octets) or all(octet == 255 for octet in octets):
        raise HeartbeatValidationError("invalid_mac", "zero/broadcast mac is not allowed")
    if octets[0] & 1:
        raise HeartbeatValidationError("invalid_mac", "multicast mac is not allowed")
    return ":".join(f"{octet:02X}" for octet in octets)


def _normalize_ip(value: Any) -> str:
    if not isinstance(value, str):
        raise HeartbeatValidationError("invalid_ip", "ip must be a string")
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise HeartbeatValidationError("invalid_ip", "ip is not valid") from exc
    if not isinstance(address, ipaddress.IPv4Address):
        raise HeartbeatValidationError("invalid_ip", "ESP32 heartbeat ip must be IPv4")
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise HeartbeatValidationError("invalid_ip", "ip must be a unicast device address")
    return str(address)


def _validate_json_shape(value: Any, *, depth: int = 0) -> None:
    if depth > 4:
        raise HeartbeatValidationError(
            "invalid_telemetry", "telemetry nesting exceeds four levels"
        )
    if isinstance(value, dict):
        if len(value) > 32:
            raise HeartbeatValidationError(
                "invalid_telemetry", "telemetry object has too many fields"
            )
        for nested in value.values():
            _validate_json_shape(nested, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > 64:
            raise HeartbeatValidationError(
                "invalid_telemetry", "telemetry array has too many items"
            )
        for nested in value:
            _validate_json_shape(nested, depth=depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise HeartbeatValidationError(
            "invalid_telemetry", "telemetry contains a non-finite number"
        )
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise HeartbeatValidationError(
            "invalid_telemetry", "telemetry contains an unsupported value"
        )


def _parse_device_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 40:
        raise HeartbeatValidationError(
            "invalid_device_time", "device_time must be a short ISO-8601 string"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HeartbeatValidationError(
            "invalid_device_time", "device_time is not valid ISO-8601"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HeartbeatValidationError(
            "invalid_device_time", "device_time must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


class MqttHeartbeatValidator:
    """Validate the v2 envelope independently from MQTT networking and storage."""

    def __init__(
        self,
        *,
        max_payload_bytes: int = MQTT_HEARTBEAT_MAX_BYTES,
        max_telemetry_bytes: int = MQTT_TELEMETRY_MAX_BYTES,
    ):
        self.max_payload_bytes = max_payload_bytes
        self.max_telemetry_bytes = max_telemetry_bytes

    def validate(self, topic: str, payload: bytes) -> HeartbeatMessage:
        if not isinstance(topic, str):
            raise HeartbeatValidationError("invalid_topic", "topic must be a string")
        match = _TOPIC_PATTERN.fullmatch(topic)
        if not match or not is_valid_device_id(match.group(1)):
            raise HeartbeatValidationError(
                "invalid_topic", "topic must be community/{device_id}/status"
            )
        topic_device_id = match.group(1)

        if not isinstance(payload, bytes):
            raise HeartbeatValidationError(
                "invalid_payload_type", "payload must be bytes"
            )
        if not payload:
            raise HeartbeatValidationError("invalid_json", "payload is empty")
        if len(payload) > self.max_payload_bytes:
            raise HeartbeatValidationError(
                "payload_too_large",
                f"payload exceeds {self.max_payload_bytes} bytes",
            )
        try:
            decoded = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise HeartbeatValidationError(
                "invalid_utf8", "payload is not valid UTF-8"
            ) from exc
        try:
            document = json.loads(
                decoded,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        except HeartbeatValidationError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise HeartbeatValidationError(
                "invalid_json", "payload is not a valid JSON object"
            ) from exc
        if not isinstance(document, dict):
            raise HeartbeatValidationError(
                "invalid_payload", "heartbeat JSON root must be an object"
            )

        missing = sorted(_REQUIRED_FIELDS - document.keys())
        if missing:
            raise HeartbeatValidationError(
                "missing_field", "missing required fields: " + ", ".join(missing)
            )
        unknown = sorted(document.keys() - _REQUIRED_FIELDS - _OPTIONAL_FIELDS)
        if unknown:
            raise HeartbeatValidationError(
                "unknown_field", "unknown fields for schema v2: " + ", ".join(unknown)
            )

        schema_version = document["schema_version"]
        if type(schema_version) is not int:
            raise HeartbeatValidationError(
                "invalid_schema_version", "schema_version must be an integer"
            )
        if schema_version != MQTT_HEARTBEAT_SCHEMA_VERSION:
            raise HeartbeatValidationError(
                "unsupported_schema_version",
                f"supported schema_version is {MQTT_HEARTBEAT_SCHEMA_VERSION}",
            )

        device_id = document["device_id"]
        if not isinstance(device_id, str) or not is_valid_device_id(device_id):
            raise HeartbeatValidationError(
                "invalid_device_id", "device_id is not stable/topic-safe"
            )
        if device_id != topic_device_id:
            raise HeartbeatValidationError(
                "device_id_mismatch", "topic and payload device_id do not match"
            )

        boot_id = document["boot_id"]
        if not isinstance(boot_id, str) or not _BOOT_ID_PATTERN.fullmatch(boot_id):
            raise HeartbeatValidationError(
                "invalid_boot_id",
                f"boot_id must be {MQTT_BOOT_ID_HEX_LENGTH} lowercase hex characters",
            )

        sequence = document["sequence"]
        if type(sequence) is not int or not 0 <= sequence <= _MAX_SQLITE_INTEGER:
            raise HeartbeatValidationError(
                "invalid_sequence", "sequence must be a non-negative 64-bit integer"
            )
        uptime_ms = document["uptime_ms"]
        if type(uptime_ms) is not int or not 0 <= uptime_ms <= _MAX_SQLITE_INTEGER:
            raise HeartbeatValidationError(
                "invalid_uptime", "uptime_ms must be a non-negative 64-bit integer"
            )

        firmware_version = document["firmware_version"]
        if (
            not isinstance(firmware_version, str)
            or not _FIRMWARE_VERSION_PATTERN.fullmatch(firmware_version)
        ):
            raise HeartbeatValidationError(
                "invalid_firmware_version", "firmware_version format is invalid"
            )
        ip_address = _normalize_ip(document["ip"])
        mac_address = _normalize_mac(document["mac"])

        telemetry = document["telemetry"]
        if not isinstance(telemetry, dict):
            raise HeartbeatValidationError(
                "invalid_telemetry", "telemetry must be a JSON object"
            )
        _validate_json_shape(telemetry)
        telemetry_size = len(
            json.dumps(
                telemetry,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if telemetry_size > self.max_telemetry_bytes:
            raise HeartbeatValidationError(
                "telemetry_too_large",
                f"telemetry exceeds {self.max_telemetry_bytes} bytes",
            )

        return HeartbeatMessage(
            device_id=device_id,
            boot_id=boot_id,
            sequence=sequence,
            firmware_version=firmware_version,
            uptime_ms=uptime_ms,
            ip_address=ip_address,
            mac_address=mac_address,
            telemetry=telemetry,
            device_time=_parse_device_time(document.get("device_time")),
        )


class MqttHeartbeatIngestor:
    """Persist validated heartbeats and enforce per-boot replay protection."""

    def __init__(
        self,
        state_service: DeviceStateService,
        validator: MqttHeartbeatValidator | None = None,
        discovery_service=None,
    ):
        self.state_service = state_service
        self.validator = validator or MqttHeartbeatValidator()
        self.discovery_service = discovery_service

    @staticmethod
    def _rejected(
        code: str,
        message: str,
        heartbeat: HeartbeatMessage | None = None,
    ) -> IngestionResult:
        return IngestionResult(
            accepted=False,
            code=code,
            message=message,
            device_id=heartbeat.device_id if heartbeat else None,
            boot_id=heartbeat.boot_id if heartbeat else None,
            sequence=heartbeat.sequence if heartbeat else None,
        )

    def ingest(
        self,
        *,
        topic: str,
        payload: bytes,
        received_at: datetime,
    ) -> IngestionResult:
        try:
            received = _utc(received_at)
            heartbeat = self.validator.validate(topic, payload)
        except HeartbeatValidationError as exc:
            return self._rejected(exc.code, exc.message)

        connection = connect_v3(self.state_service.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            profile = connection.execute(
                "SELECT identity_kind, identity_value FROM v3_device_profiles "
                "WHERE device_id = ?",
                (heartbeat.device_id,),
            ).fetchone()
            if not profile:
                connection.rollback()
                if self.discovery_service is not None:
                    try:
                        metadata = {
                            "firmware_version": heartbeat.firmware_version,
                            "sequence": heartbeat.sequence,
                            "boot_id_digest": sha256(heartbeat.boot_id.encode("ascii")).hexdigest()[:16],
                        }
                        device_type_hint = heartbeat.telemetry.get("device_type")
                        if isinstance(device_type_hint, str) and re.fullmatch(
                            r"[a-z0-9][a-z0-9_-]{0,63}", device_type_hint
                        ):
                            metadata["device_type_hint"] = device_type_hint
                        discovery = self.discovery_service.observe(
                            source="mqtt_unknown",
                            mac_address=heartbeat.mac_address,
                            ip_address=heartbeat.ip_address,
                            proposed_device_id=heartbeat.device_id,
                            observed_at=heartbeat.device_time or received,
                            received_at=received,
                            deduplication_key=f"{heartbeat.boot_id}:{heartbeat.sequence}",
                            sanitized_metadata=metadata,
                        )
                        if discovery["code"] == "candidate_capacity_reached":
                            return self._rejected(
                                "unknown_device_discovery_degraded",
                                "unknown device candidate capacity is unavailable",
                                heartbeat,
                            )
                        return IngestionResult(
                            accepted=False,
                            code="unknown_device_discovered",
                            message="device is quarantined for administrator verification",
                            device_id=heartbeat.device_id,
                            boot_id=heartbeat.boot_id,
                            sequence=heartbeat.sequence,
                            candidate_id=discovery.get("candidate_id"),
                        )
                    except Exception:
                        try:
                            self.discovery_service.mark_degraded("discovery_storage_error")
                        except Exception:
                            pass
                        return self._rejected(
                            "unknown_device_discovery_unavailable",
                            "unknown device evidence could not be stored",
                            heartbeat,
                        )
                return self._rejected(
                    "unknown_device",
                    "device_id is not registered; no trusted device was created",
                    heartbeat,
                )
            if (
                profile["identity_kind"] != "mac"
                or profile["identity_value"] != heartbeat.mac_address
            ):
                connection.rollback()
                return self._rejected(
                    "identity_mismatch",
                    "heartbeat MAC does not match the registered device identity",
                    heartbeat,
                )

            cursor = connection.execute(
                "SELECT current_boot_id FROM v3_mqtt_device_cursors "
                "WHERE device_id = ?",
                (heartbeat.device_id,),
            ).fetchone()
            session = connection.execute(
                "SELECT last_sequence, last_uptime_ms FROM v3_mqtt_boot_sessions "
                "WHERE device_id = ? AND boot_id = ?",
                (heartbeat.device_id, heartbeat.boot_id),
            ).fetchone()
            received_text = _iso(received)

            if cursor and cursor["current_boot_id"] == heartbeat.boot_id:
                if not session:
                    connection.rollback()
                    return self._rejected(
                        "database_state_error",
                        "current boot cursor has no matching session",
                        heartbeat,
                    )
                if heartbeat.sequence == session["last_sequence"]:
                    connection.rollback()
                    return self._rejected(
                        "duplicate_sequence",
                        "sequence was already accepted for this boot",
                        heartbeat,
                    )
                if heartbeat.sequence < session["last_sequence"]:
                    connection.rollback()
                    return self._rejected(
                        "out_of_order_sequence",
                        "sequence is older than the accepted boot cursor",
                        heartbeat,
                    )
                if heartbeat.uptime_ms < session["last_uptime_ms"]:
                    connection.rollback()
                    return self._rejected(
                        "uptime_regression",
                        "uptime_ms moved backwards within one boot session",
                        heartbeat,
                    )
                new_session = False
            else:
                if session:
                    connection.rollback()
                    return self._rejected(
                        "replayed_boot",
                        "message belongs to an older boot session",
                        heartbeat,
                    )
                if heartbeat.sequence not in MQTT_INITIAL_SEQUENCE_VALUES:
                    connection.rollback()
                    return self._rejected(
                        "invalid_initial_sequence",
                        "a new boot must start with sequence 0 or 1",
                        heartbeat,
                    )
                new_session = True
                connection.execute(
                    "INSERT INTO v3_mqtt_boot_sessions "
                    "(device_id, boot_id, first_received_at, last_received_at, "
                    "last_sequence, last_uptime_ms, firmware_version) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        heartbeat.device_id,
                        heartbeat.boot_id,
                        received_text,
                        received_text,
                        heartbeat.sequence,
                        heartbeat.uptime_ms,
                        heartbeat.firmware_version,
                    ),
                )
                connection.execute(
                    "INSERT INTO v3_mqtt_device_cursors "
                    "(device_id, current_boot_id, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(device_id) DO UPDATE SET "
                    "current_boot_id = excluded.current_boot_id, "
                    "updated_at = excluded.updated_at",
                    (heartbeat.device_id, heartbeat.boot_id, received_text),
                )

            observation = self.state_service.record_observation_in_transaction(
                connection,
                device_id=heartbeat.device_id,
                identity_kind="mac",
                identity_value=heartbeat.mac_address,
                source=f"mqtt:{heartbeat.boot_id}",
                ip_address=heartbeat.ip_address,
                observed_at=heartbeat.device_time,
                received_at=received,
                sequence=heartbeat.sequence,
                payload=heartbeat.telemetry,
                boot_id=heartbeat.boot_id,
                firmware_version=heartbeat.firmware_version,
                uptime_ms=heartbeat.uptime_ms,
            )
            if not new_session:
                connection.execute(
                    "UPDATE v3_mqtt_boot_sessions SET last_received_at = ?, "
                    "last_sequence = ?, last_uptime_ms = ?, firmware_version = ? "
                    "WHERE device_id = ? AND boot_id = ?",
                    (
                        received_text,
                        heartbeat.sequence,
                        heartbeat.uptime_ms,
                        heartbeat.firmware_version,
                        heartbeat.device_id,
                        heartbeat.boot_id,
                    ),
                )
                connection.execute(
                    "UPDATE v3_mqtt_device_cursors SET updated_at = ? "
                    "WHERE device_id = ?",
                    (received_text, heartbeat.device_id),
                )
            connection.commit()
            return IngestionResult(
                accepted=True,
                code="accepted",
                message="heartbeat accepted",
                device_id=heartbeat.device_id,
                boot_id=heartbeat.boot_id,
                sequence=heartbeat.sequence,
                observation_id=observation["observation_id"],
                state_version=observation["state_version"],
            )
        except DeviceNotFoundError:
            connection.rollback()
            return self._rejected(
                "unknown_device", "device disappeared before state write", heartbeat
            )
        except DeviceIdentityConflictError:
            connection.rollback()
            return self._rejected(
                "identity_mismatch", "device identity changed during state write", heartbeat
            )
        except sqlite3.IntegrityError:
            connection.rollback()
            return self._rejected(
                "database_constraint", "database constraint rejected heartbeat", heartbeat
            )
        except (sqlite3.DatabaseError, DeviceStateError):
            connection.rollback()
            return self._rejected(
                "database_error", "heartbeat could not be persisted", heartbeat
            )
        finally:
            connection.close()
