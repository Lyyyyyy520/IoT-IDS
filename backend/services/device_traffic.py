"""Stable-device traffic aggregation, history queries, and bounded live rates.

The v3 store keeps aggregate metadata only. Packet payloads, flags, cookies,
credentials, and authentication material are deliberately outside this model.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import ipaddress
import logging
from pathlib import Path
import re
import sqlite3
import threading
from typing import Callable, Iterator, Mapping, Sequence

from contracts import DetectionReadiness
from services.realtime_events import V3DatabaseUnavailable
from v3_database import V3_DEVICE_TRAFFIC_MIGRATION, connect_v3_existing


LOGGER = logging.getLogger(__name__)
TRAFFIC_COMPONENT_ID = "traffic-aggregation"
TRAFFIC_RETENTION_DAYS = 30
TRAFFIC_MAX_BATCH_SAMPLES = 1000
TRAFFIC_MAX_QUERY_POINTS = 2000
TRAFFIC_MAX_FUTURE_SECONDS = 300
TRAFFIC_MAX_BYTES = 64 * 1024 * 1024
TRAFFIC_MAX_COUNT = 10_000_000

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROTOCOL = re.compile(r"^[A-Z0-9][A-Z0-9._+-]{0,31}$")
_DIRECTIONS = frozenset({"tx", "rx"})
_RESOLUTIONS = {"minute": 60, "5minute": 300, "hour": 3600}


class TrafficError(ValueError):
    code = "traffic_error"


class TrafficValidationError(TrafficError):
    code = "invalid_traffic_sample"

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TrafficQueryError(TrafficError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TrafficDeviceNotFound(TrafficError):
    code = "device_not_found"


class TrafficStoreUnavailable(V3DatabaseUnavailable):
    code = "traffic_store_unavailable"


def _utc(value: datetime, field: str = "timestamp") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TrafficValidationError("timezone_required", f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def _timestamp(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        return _utc(value, field)
    if not isinstance(value, str) or not value.strip():
        raise TrafficValidationError("invalid_timestamp", f"{field} must be an ISO 8601 timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise TrafficValidationError("invalid_timestamp", f"{field} is not valid ISO 8601") from exc
    return _utc(parsed, field)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value.strip()):
        raise TrafficValidationError("invalid_identifier", f"{field} is invalid")
    return value.strip()


def _ip(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TrafficValidationError("invalid_ip", f"{field} must be an IP address")
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError as exc:
        raise TrafficValidationError("invalid_ip", f"{field} must be an IP address") from exc


def _integer(value: object, field: str, *, maximum: int) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise TrafficValidationError(
            "invalid_counter", f"{field} must be a non-negative integer <= {maximum}"
        )
    return value


def _port(value: object, field: str) -> int | None:
    if value is None or value == 0:
        return None
    if type(value) is not int or not 1 <= value <= 65535:
        raise TrafficValidationError("invalid_port", f"{field} must be between 1 and 65535")
    return value


def _protocol(value: object, field: str = "network_protocol") -> str:
    if not isinstance(value, str):
        raise TrafficValidationError("invalid_protocol", f"{field} must be a string")
    normalized = value.strip().upper()
    if not _PROTOCOL.fullmatch(normalized):
        raise TrafficValidationError("invalid_protocol", f"{field} is invalid")
    return normalized


def _bucket(value: datetime, seconds: int = 60) -> datetime:
    current = _utc(value)
    epoch = int(current.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=timezone.utc)


@dataclass(frozen=True)
class TrafficSample:
    source_id: str
    sample_id: str
    occurred_at: datetime
    received_at: datetime
    src_ip: str
    dst_ip: str
    network_protocol: str
    application_protocol: str | None
    application_protocol_inferred: bool
    src_port: int | None
    dst_port: int | None
    bytes: int
    packets: int
    flow_count: int

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
        *,
        source_id: str,
        received_at: datetime,
    ) -> "TrafficSample":
        if not isinstance(value, Mapping):
            raise TrafficValidationError("invalid_sample", "traffic sample must be an object")
        allowed = {
            "sample_id", "occurred_at", "src_ip", "dst_ip", "network_protocol",
            "application_protocol", "application_protocol_inferred", "src_port",
            "dst_port", "bytes", "packets", "flow_count",
        }
        if set(value) - allowed:
            raise TrafficValidationError("unknown_sample_fields", "traffic sample has unknown fields")
        application = value.get("application_protocol")
        inferred = value.get("application_protocol_inferred", False)
        if type(inferred) is not bool:
            raise TrafficValidationError(
                "invalid_protocol_evidence", "application_protocol_inferred must be boolean"
            )
        if application is not None:
            application = _protocol(application, "application_protocol")
        if inferred and application is None:
            raise TrafficValidationError(
                "invalid_protocol_evidence", "inferred application protocol requires a value"
            )
        return cls(
            source_id=_identifier(source_id, "source_id"),
            sample_id=_identifier(value.get("sample_id"), "sample_id"),
            occurred_at=_timestamp(value.get("occurred_at"), "occurred_at"),
            received_at=_utc(received_at, "received_at"),
            src_ip=_ip(value.get("src_ip"), "src_ip"),
            dst_ip=_ip(value.get("dst_ip"), "dst_ip"),
            network_protocol=_protocol(value.get("network_protocol")),
            application_protocol=application,
            application_protocol_inferred=inferred,
            src_port=_port(value.get("src_port"), "src_port"),
            dst_port=_port(value.get("dst_port"), "dst_port"),
            bytes=_integer(value.get("bytes"), "bytes", maximum=TRAFFIC_MAX_BYTES),
            packets=_integer(value.get("packets"), "packets", maximum=TRAFFIC_MAX_COUNT),
            flow_count=_integer(value.get("flow_count"), "flow_count", maximum=TRAFFIC_MAX_COUNT),
        )


def ensure_traffic_schema(connection: sqlite3.Connection) -> None:
    """Verify migration v5 without creating or repairing objects."""
    try:
        migration = connection.execute(
            "SELECT name, checksum FROM v3_schema_migrations WHERE version = 5"
        ).fetchone()
        required = {
            "v3_device_ip_bindings", "v3_device_traffic_minutes",
            "v3_device_traffic_protocol_minutes", "v3_device_traffic_peer_minutes",
            "v3_traffic_ingest_batches", "v3_traffic_ingest_samples",
            "v3_traffic_unassigned_minutes",
        }
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    except sqlite3.Error as exc:
        raise TrafficStoreUnavailable("v3 traffic schema is unavailable") from exc
    if not migration or (
        migration["name"] != V3_DEVICE_TRAFFIC_MIGRATION.name
        or migration["checksum"] != V3_DEVICE_TRAFFIC_MIGRATION.checksum
    ):
        raise TrafficStoreUnavailable("v3 traffic migration has not been applied")
    if not required <= tables:
        raise TrafficStoreUnavailable("v3 traffic schema is incomplete")


def record_ip_binding(
    connection: sqlite3.Connection,
    *,
    device_id: str,
    ip_address: str | None,
    valid_from: datetime,
    source: str,
    source_observation_id: int | None,
    created_at: datetime | None = None,
) -> dict:
    """Advance one device's half-open IP history inside the caller transaction."""
    if ip_address is None:
        return {"changed": False, "reason_code": "ip_missing"}
    normalized_ip = _ip(ip_address, "ip_address")
    effective = _utc(valid_from, "valid_from")
    effective_text = _iso(effective)
    current = connection.execute(
        "SELECT binding_id, ip_address, valid_from FROM v3_device_ip_bindings "
        "WHERE device_id = ? AND valid_to IS NULL", (device_id,),
    ).fetchone()
    if current and current["ip_address"] == normalized_ip:
        return {"changed": False, "reason_code": "unchanged"}
    if current and effective_text <= current["valid_from"]:
        return {"changed": False, "reason_code": "binding_time_regression"}
    if current:
        connection.execute(
            "UPDATE v3_device_ip_bindings SET valid_to = ? WHERE binding_id = ?",
            (effective_text, current["binding_id"]),
        )
    connection.execute(
        "INSERT INTO v3_device_ip_bindings "
        "(device_id, ip_address, valid_from, valid_to, source, "
        "source_observation_id, created_at) VALUES (?, ?, ?, NULL, ?, ?, ?)",
        (device_id, normalized_ip, effective_text, source.strip(),
         source_observation_id, _iso(created_at or effective)),
    )
    matches = connection.execute(
        "SELECT COUNT(DISTINCT device_id) FROM v3_device_ip_bindings "
        "WHERE ip_address = ? AND valid_from <= ? "
        "AND (valid_to IS NULL OR valid_to > ?)",
        (normalized_ip, effective_text, effective_text),
    ).fetchone()[0]
    return {
        "changed": True,
        "reason_code": "ip_binding_conflict" if matches > 1 else "binding_created",
    }


def resolve_ip_binding(
    connection: sqlite3.Connection,
    ip_address: str,
    occurred_at: datetime,
) -> tuple[str, str | None]:
    at = _iso(occurred_at)
    rows = connection.execute(
        "SELECT DISTINCT device_id FROM v3_device_ip_bindings "
        "WHERE ip_address = ? AND valid_from <= ? "
        "AND (valid_to IS NULL OR valid_to > ?) ORDER BY device_id",
        (ip_address, at, at),
    ).fetchall()
    if not rows:
        return "unknown_ip", None
    if len(rows) > 1:
        return "ambiguous_ip_binding", None
    return "resolved", rows[0]["device_id"]


class RealtimeTrafficWindow:
    """Bounded in-process per-second rates; never a historical fact source."""

    def __init__(
        self,
        *,
        retention_seconds: int = 120,
        max_devices: int = 512,
        max_buckets_per_device: int = 300,
        clock: Callable[[], datetime] | None = None,
    ):
        if not 60 <= retention_seconds <= 300:
            raise ValueError("retention_seconds must be between 60 and 300")
        if max_devices <= 0 or max_buckets_per_device <= 0:
            raise ValueError("realtime traffic bounds must be positive")
        self.retention_seconds = retention_seconds
        self.max_devices = max_devices
        self.max_buckets_per_device = max_buckets_per_device
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._devices: OrderedDict[str, OrderedDict[int, dict]] = OrderedDict()
        self._lock = threading.RLock()

    def _now(self) -> datetime:
        return _utc(self._clock(), "realtime clock")

    def _prune(self, now_epoch: int) -> None:
        cutoff = now_epoch - self.retention_seconds + 1
        empty = []
        for device_id, buckets in self._devices.items():
            while buckets and next(iter(buckets)) < cutoff:
                buckets.popitem(last=False)
            if not buckets:
                empty.append(device_id)
        for device_id in empty:
            self._devices.pop(device_id, None)

    def add(self, device_id: str, direction: str, sample: TrafficSample) -> None:
        if direction not in _DIRECTIONS:
            raise ValueError("invalid realtime traffic direction")
        epoch = int(sample.occurred_at.timestamp())
        now_epoch = int(self._now().timestamp())
        if epoch < now_epoch - self.retention_seconds + 1 or epoch > now_epoch + 1:
            return
        with self._lock:
            self._prune(now_epoch)
            buckets = self._devices.setdefault(device_id, OrderedDict())
            self._devices.move_to_end(device_id)
            current = buckets.setdefault(epoch, {
                "tx_bytes": 0, "rx_bytes": 0,
                "tx_packets": 0, "rx_packets": 0,
                "tx_flows": 0, "rx_flows": 0,
                "protocols": {},
            })
            current[f"{direction}_bytes"] += sample.bytes
            current[f"{direction}_packets"] += sample.packets
            current[f"{direction}_flows"] += sample.flow_count
            protocol = current["protocols"].setdefault(sample.network_protocol, {
                "tx_bytes": 0, "rx_bytes": 0,
                "tx_packets": 0, "rx_packets": 0,
                "tx_flows": 0, "rx_flows": 0,
            })
            protocol[f"{direction}_bytes"] += sample.bytes
            protocol[f"{direction}_packets"] += sample.packets
            protocol[f"{direction}_flows"] += sample.flow_count
            while len(buckets) > self.max_buckets_per_device:
                buckets.popitem(last=False)
            while len(self._devices) > self.max_devices:
                self._devices.popitem(last=False)

    def snapshot(self, device_id: str, *, protocol: str | None = None) -> dict:
        now = self._now()
        now_epoch = int(now.timestamp())
        with self._lock:
            self._prune(now_epoch)
            buckets = self._devices.get(device_id)
            if not buckets:
                return {
                    "available": False,
                    "readiness": DetectionReadiness.WARMING_UP.value,
                    "reason": "realtime_window_empty_after_restart_or_no_recent_samples",
                    "window_seconds": self.retention_seconds,
                    "as_of": _iso(now),
                }
            selected = []
            for value in buckets.values():
                if protocol is None:
                    selected.append(value)
                elif protocol in value["protocols"]:
                    selected.append(value["protocols"][protocol])
            if not selected:
                return {
                    "available": False,
                    "readiness": DetectionReadiness.WARMING_UP.value,
                    "reason": "no_recent_samples_for_protocol",
                    "window_seconds": self.retention_seconds,
                    "as_of": _iso(now),
                }
            first_epoch = next(iter(buckets))
            duration = max(1, min(self.retention_seconds, now_epoch - first_epoch + 1))
            totals = {
                key: sum(item[key] for item in selected)
                for key in (
                    "tx_bytes", "rx_bytes", "tx_packets", "rx_packets",
                    "tx_flows", "rx_flows",
                )
            }
            return {
                "available": True,
                "readiness": DetectionReadiness.READY.value,
                "reason": None,
                "window_seconds": duration,
                "as_of": _iso(now),
                "tx_bytes_per_second": totals["tx_bytes"] / duration,
                "rx_bytes_per_second": totals["rx_bytes"] / duration,
                "tx_packets_per_second": totals["tx_packets"] / duration,
                "rx_packets_per_second": totals["rx_packets"] / duration,
                "tx_flows_per_second": totals["tx_flows"] / duration,
                "rx_flows_per_second": totals["rx_flows"] / duration,
            }

    def stats(self) -> dict:
        with self._lock:
            return {
                "device_count": len(self._devices),
                "bucket_count": sum(len(item) for item in self._devices.values()),
            }


class DeviceTrafficService:
    def __init__(
        self,
        database_path: str | Path | None,
        *,
        realtime_window: RealtimeTrafficWindow | None = None,
        clock: Callable[[], datetime] | None = None,
        max_batch_samples: int = TRAFFIC_MAX_BATCH_SAMPLES,
        retention_days: int = TRAFFIC_RETENTION_DAYS,
        max_future_seconds: int = TRAFFIC_MAX_FUTURE_SECONDS,
        fault_injector: Callable[[sqlite3.Connection], None] | None = None,
    ):
        self.database_path = Path(database_path) if database_path else None
        self.realtime_window = realtime_window or RealtimeTrafficWindow(clock=clock)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_batch_samples = max_batch_samples
        self.retention_days = retention_days
        self.max_future_seconds = max_future_seconds
        self._fault_injector = fault_injector

    def _now(self) -> datetime:
        return _utc(self._clock(), "traffic clock")

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        if self.database_path is None:
            raise TrafficStoreUnavailable("traffic database path is not configured")
        connection = None
        try:
            connection = connect_v3_existing(self.database_path)
            ensure_traffic_schema(connection)
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except (FileNotFoundError, sqlite3.Error) as exc:
            if connection is not None and write:
                connection.rollback()
            raise TrafficStoreUnavailable("traffic database is unavailable") from exc
        except Exception:
            if connection is not None and write:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def _set_health(self, readiness: str, reason: str | None) -> None:
        if self.database_path is None:
            return
        try:
            from services.device_state import DeviceStateService
            DeviceStateService(
                self.database_path, create_if_missing=False
            ).set_component_readiness(TRAFFIC_COMPONENT_ID, readiness, reason)
        except Exception as exc:
            LOGGER.warning(
                "traffic_health_update_failed code=health_store_failure type=%s",
                type(exc).__name__,
            )

    def mark_degraded(self, reason: str) -> None:
        """Expose a payload-free component-health boundary to source adapters."""
        self._set_health(DetectionReadiness.DEGRADED.value, reason)

    def _validate_age(self, sample: TrafficSample, now: datetime) -> None:
        if sample.occurred_at > now + timedelta(seconds=self.max_future_seconds):
            raise TrafficValidationError(
                "sample_too_far_in_future", "occurred_at is too far in the future"
            )
        if sample.occurred_at < now - timedelta(days=self.retention_days):
            raise TrafficValidationError(
                "sample_outside_retention", "occurred_at is outside retention"
            )

    @staticmethod
    def _aggregate_minute(
        connection: sqlite3.Connection,
        *,
        device_id: str,
        direction: str,
        peer_ip: str,
        peer_device_id: str | None,
        sample: TrafficSample,
        updated_at: str,
    ) -> None:
        bucket = _iso(_bucket(sample.occurred_at))
        occurred = _iso(sample.occurred_at)
        tx = direction == "tx"
        connection.execute(
            "INSERT INTO v3_device_traffic_minutes "
            "(device_id, bucket_start, tx_bytes, rx_bytes, tx_packets, rx_packets, "
            "tx_flow_count, rx_flow_count, first_sample_at, last_sample_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(device_id, bucket_start) DO UPDATE SET "
            "tx_bytes = tx_bytes + excluded.tx_bytes, "
            "rx_bytes = rx_bytes + excluded.rx_bytes, "
            "tx_packets = tx_packets + excluded.tx_packets, "
            "rx_packets = rx_packets + excluded.rx_packets, "
            "tx_flow_count = tx_flow_count + excluded.tx_flow_count, "
            "rx_flow_count = rx_flow_count + excluded.rx_flow_count, "
            "first_sample_at = MIN(first_sample_at, excluded.first_sample_at), "
            "last_sample_at = MAX(last_sample_at, excluded.last_sample_at), "
            "updated_at = excluded.updated_at",
            (
                device_id, bucket,
                sample.bytes if tx else 0, sample.bytes if not tx else 0,
                sample.packets if tx else 0, sample.packets if not tx else 0,
                sample.flow_count if tx else 0, sample.flow_count if not tx else 0,
                occurred, occurred, updated_at,
            ),
        )
        connection.execute(
            "INSERT INTO v3_device_traffic_protocol_minutes "
            "(device_id, bucket_start, direction, protocol, bytes, packets, flow_count, "
            "first_sample_at, last_sample_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(device_id, bucket_start, direction, protocol) DO UPDATE SET "
            "bytes = bytes + excluded.bytes, packets = packets + excluded.packets, "
            "flow_count = flow_count + excluded.flow_count, "
            "first_sample_at = MIN(first_sample_at, excluded.first_sample_at), "
            "last_sample_at = MAX(last_sample_at, excluded.last_sample_at), "
            "updated_at = excluded.updated_at",
            (
                device_id, bucket, direction, sample.network_protocol,
                sample.bytes, sample.packets, sample.flow_count,
                occurred, occurred, updated_at,
            ),
        )
        peer_key = f"device:{peer_device_id}" if peer_device_id else f"ip:{peer_ip}"
        connection.execute(
            "INSERT INTO v3_device_traffic_peer_minutes "
            "(device_id, bucket_start, direction, peer_key, peer_device_id, peer_ip, "
            "protocol, bytes, packets, flow_count, first_sample_at, last_sample_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(device_id, bucket_start, direction, peer_key, protocol) DO UPDATE SET "
            "bytes = bytes + excluded.bytes, packets = packets + excluded.packets, "
            "flow_count = flow_count + excluded.flow_count, "
            "first_sample_at = MIN(first_sample_at, excluded.first_sample_at), "
            "last_sample_at = MAX(last_sample_at, excluded.last_sample_at), "
            "updated_at = excluded.updated_at",
            (
                device_id, bucket, direction, peer_key, peer_device_id, peer_ip,
                sample.network_protocol, sample.bytes, sample.packets, sample.flow_count,
                occurred, occurred, updated_at,
            ),
        )

    @staticmethod
    def _record_unassigned(
        connection: sqlite3.Connection,
        *,
        source_id: str,
        reason_code: str,
        sample: TrafficSample,
        updated_at: str,
    ) -> None:
        bucket = _iso(_bucket(sample.occurred_at))
        occurred = _iso(sample.occurred_at)
        connection.execute(
            "INSERT INTO v3_traffic_unassigned_minutes "
            "(source_id, bucket_start, reason_code, sample_count, bytes, packets, "
            "first_sample_at, last_sample_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source_id, bucket_start, reason_code) DO UPDATE SET "
            "sample_count = sample_count + 1, bytes = bytes + excluded.bytes, "
            "packets = packets + excluded.packets, "
            "first_sample_at = MIN(first_sample_at, excluded.first_sample_at), "
            "last_sample_at = MAX(last_sample_at, excluded.last_sample_at), "
            "updated_at = excluded.updated_at",
            (source_id, bucket, reason_code, sample.bytes, sample.packets,
             occurred, occurred, updated_at),
        )

    def ingest_batch(
        self,
        *,
        source_id: str,
        source_session_id: str,
        batch_id: str,
        batch_sequence: int,
        samples: Sequence[TrafficSample | Mapping[str, object]],
        received_at: datetime | None = None,
    ) -> dict:
        source_id = _identifier(source_id, "source_id")
        source_session_id = _identifier(source_session_id, "source_session_id")
        batch_id = _identifier(batch_id, "batch_id")
        batch_sequence = _integer(
            batch_sequence, "batch_sequence", maximum=(1 << 63) - 1
        )
        if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes, bytearray)):
            raise TrafficValidationError("invalid_batch", "samples must be an array")
        if not samples:
            raise TrafficValidationError("empty_batch", "traffic batch must not be empty")
        if len(samples) > self.max_batch_samples:
            raise TrafficValidationError("batch_too_large", "traffic batch exceeds the configured limit")
        received = _utc(received_at or self._now(), "received_at")
        now = self._now()
        valid: list[TrafficSample] = []
        rejected: list[dict] = []
        seen_ids: set[str] = set()
        for index, raw in enumerate(samples):
            try:
                sample = raw if isinstance(raw, TrafficSample) else TrafficSample.from_mapping(
                    raw, source_id=source_id, received_at=received
                )
                if sample.source_id != source_id:
                    raise TrafficValidationError(
                        "source_id_mismatch", "sample source_id does not match batch"
                    )
                self._validate_age(sample, now)
                if sample.sample_id in seen_ids:
                    raise TrafficValidationError(
                        "duplicate_sample_in_batch", "sample_id is duplicated in batch"
                    )
                seen_ids.add(sample.sample_id)
                valid.append(sample)
            except TrafficValidationError as exc:
                rejected.append({"index": index, "code": exc.code})

        window_updates: list[tuple[str, str, TrafficSample]] = []
        try:
            with self._connection(write=True) as connection:
                duplicate = connection.execute(
                    "SELECT * FROM v3_traffic_ingest_batches WHERE "
                    "source_id = ? AND source_session_id = ? "
                    "AND (batch_id = ? OR batch_sequence = ?)",
                    (source_id, source_session_id, batch_id, batch_sequence),
                ).fetchone()
                if duplicate:
                    return {
                        "status": "duplicate",
                        "reason_code": (
                            "duplicate_batch" if duplicate["batch_id"] == batch_id
                            else "duplicate_batch_sequence"
                        ),
                        "accepted_samples": int(duplicate["accepted_samples"]),
                        "rejected_samples": int(duplicate["rejected_samples"]),
                        "duplicate_samples": int(duplicate["duplicate_samples"]),
                        "unassigned_samples": int(duplicate["unassigned_samples"]),
                        "errors": [],
                    }
                created = _iso(now)
                connection.execute(
                    "INSERT INTO v3_traffic_ingest_batches "
                    "(source_id, source_session_id, batch_id, batch_sequence, received_at, "
                    "accepted_samples, rejected_samples, duplicate_samples, unassigned_samples, "
                    "status, created_at) VALUES (?, ?, ?, ?, ?, 0, ?, 0, 0, 'rejected', ?)",
                    (source_id, source_session_id, batch_id, batch_sequence,
                     _iso(received), len(rejected), created),
                )
                accepted = 0
                duplicate_samples = 0
                unassigned = 0
                sample_times: list[datetime] = []
                for sample in valid:
                    if connection.execute(
                        "SELECT 1 FROM v3_traffic_ingest_samples WHERE "
                        "source_id = ? AND source_session_id = ? AND sample_id = ?",
                        (source_id, source_session_id, sample.sample_id),
                    ).fetchone():
                        duplicate_samples += 1
                        continue
                    connection.execute(
                        "INSERT INTO v3_traffic_ingest_samples "
                        "(source_id, source_session_id, sample_id, batch_id, occurred_at, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (source_id, source_session_id, sample.sample_id, batch_id,
                         _iso(sample.occurred_at), created),
                    )
                    accepted += 1
                    sample_times.append(sample.occurred_at)
                    src_status, src_device = resolve_ip_binding(
                        connection, sample.src_ip, sample.occurred_at
                    )
                    dst_status, dst_device = resolve_ip_binding(
                        connection, sample.dst_ip, sample.occurred_at
                    )
                    endpoints: list[tuple[str, str, str, str | None]] = []
                    if src_device:
                        endpoints.append((src_device, "tx", sample.dst_ip, dst_device))
                    if dst_device:
                        endpoints.append((dst_device, "rx", sample.src_ip, src_device))
                    for device_id, direction, peer_ip, peer_device in endpoints:
                        self._aggregate_minute(
                            connection, device_id=device_id, direction=direction,
                            peer_ip=peer_ip, peer_device_id=peer_device,
                            sample=sample, updated_at=created,
                        )
                        window_updates.append((device_id, direction, sample))
                    ambiguous = "ambiguous_ip_binding" in {src_status, dst_status}
                    if not endpoints or ambiguous:
                        unassigned += 1
                        self._record_unassigned(
                            connection, source_id=source_id,
                            reason_code=(
                                "ambiguous_ip_binding" if ambiguous
                                else "no_managed_endpoint"
                            ),
                            sample=sample, updated_at=created,
                        )
                status = "committed"
                if accepted == 0:
                    status = "rejected"
                elif rejected or duplicate_samples or unassigned:
                    status = "partial"
                first = _iso(min(sample_times)) if sample_times else None
                last = _iso(max(sample_times)) if sample_times else None
                connection.execute(
                    "UPDATE v3_traffic_ingest_batches SET first_sample_at = ?, last_sample_at = ?, "
                    "accepted_samples = ?, rejected_samples = ?, duplicate_samples = ?, "
                    "unassigned_samples = ?, status = ? WHERE source_id = ? "
                    "AND source_session_id = ? AND batch_id = ?",
                    (first, last, accepted, len(rejected), duplicate_samples,
                     unassigned, status, source_id, source_session_id, batch_id),
                )
                if self._fault_injector is not None:
                    self._fault_injector(connection)
            for device_id, direction, sample in window_updates:
                self.realtime_window.add(device_id, direction, sample)
            self._set_health(DetectionReadiness.READY.value, None)
            return {
                "status": status,
                "reason_code": None,
                "accepted_samples": accepted,
                "rejected_samples": len(rejected),
                "duplicate_samples": duplicate_samples,
                "unassigned_samples": unassigned,
                "errors": rejected,
            }
        except TrafficStoreUnavailable:
            self._set_health(
                DetectionReadiness.DEGRADED.value, "traffic_store_unavailable"
            )
            raise
        except Exception as exc:
            self._set_health(
                DetectionReadiness.DEGRADED.value, "aggregation_transaction_failed"
            )
            LOGGER.error(
                "traffic_aggregation_failed code=aggregation_transaction_failed type=%s",
                type(exc).__name__,
            )
            raise

    @staticmethod
    def _require_device(connection: sqlite3.Connection, device_id: str) -> None:
        if connection.execute(
            "SELECT 1 FROM v3_device_profiles WHERE device_id = ?", (device_id,)
        ).fetchone() is None:
            raise TrafficDeviceNotFound(f"device {device_id} does not exist")

    @staticmethod
    def _resolution(
        requested: str, start: datetime, end: datetime, max_points: int
    ) -> tuple[str, int]:
        if requested == "auto":
            span = (end - start).total_seconds()
            requested = "minute" if span <= 6 * 3600 else (
                "5minute" if span <= 2 * 86400 else "hour"
            )
        if requested not in _RESOLUTIONS:
            raise TrafficQueryError(
                "invalid_resolution", "resolution must be auto, minute, 5minute, or hour"
            )
        seconds = _RESOLUTIONS[requested]
        possible_points = int((end - start).total_seconds() // seconds) + 1
        if possible_points > max_points:
            raise TrafficQueryError(
                "too_many_data_points", "query would exceed the maximum data point limit"
            )
        return requested, seconds

    def _query_bounds(
        self,
        start: datetime,
        end: datetime,
        resolution: str,
        max_points: int,
    ) -> tuple[datetime, datetime, str, int]:
        normalized_start = _utc(start, "from")
        normalized_end = _utc(end, "to")
        if normalized_end <= normalized_start:
            raise TrafficQueryError("invalid_time_range", "to must be later than from")
        if normalized_end - normalized_start > timedelta(days=self.retention_days):
            raise TrafficQueryError(
                "query_range_too_large", "traffic queries are limited to 30 days"
            )
        selected, seconds = self._resolution(
            resolution, normalized_start, normalized_end, max_points
        )
        return normalized_start, normalized_end, selected, seconds

    @staticmethod
    def _empty_totals() -> dict[str, int]:
        return {
            "tx_bytes": 0, "rx_bytes": 0,
            "tx_packets": 0, "rx_packets": 0,
            "tx_flows": 0, "rx_flows": 0,
        }

    def query_traffic(
        self,
        device_id: str,
        *,
        start: datetime,
        end: datetime,
        resolution: str = "auto",
        protocol: str | None = None,
        max_points: int = TRAFFIC_MAX_QUERY_POINTS,
    ) -> dict:
        device_id = _identifier(device_id, "device_id")
        start, end, selected_resolution, seconds = self._query_bounds(
            start, end, resolution, max_points
        )
        selected_protocol = _protocol(protocol) if protocol else None
        start_text, end_text = _iso(start), _iso(end)
        with self._connection() as connection:
            self._require_device(connection, device_id)
            if selected_protocol:
                rows = connection.execute(
                    "SELECT bucket_start, direction, bytes, packets, flow_count, "
                    "first_sample_at, last_sample_at FROM v3_device_traffic_protocol_minutes "
                    "WHERE device_id = ? AND bucket_start >= ? AND bucket_start < ? "
                    "AND protocol = ? ORDER BY bucket_start, direction",
                    (device_id, start_text, end_text, selected_protocol),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT bucket_start, tx_bytes, rx_bytes, tx_packets, rx_packets, "
                    "tx_flow_count, rx_flow_count, first_sample_at, last_sample_at "
                    "FROM v3_device_traffic_minutes WHERE device_id = ? "
                    "AND bucket_start >= ? AND bucket_start < ? ORDER BY bucket_start",
                    (device_id, start_text, end_text),
                ).fetchall()

            grouped: dict[str, dict] = {}
            latest_sample: str | None = None
            for row in rows:
                bucket = _bucket(_timestamp(row["bucket_start"], "bucket_start"), seconds)
                key = _iso(bucket)
                point = grouped.setdefault(key, {
                    "bucket_start": key,
                    **self._empty_totals(),
                })
                if selected_protocol:
                    direction = row["direction"]
                    point[f"{direction}_bytes"] += int(row["bytes"])
                    point[f"{direction}_packets"] += int(row["packets"])
                    point[f"{direction}_flows"] += int(row["flow_count"])
                else:
                    for output, column in (
                        ("tx_bytes", "tx_bytes"), ("rx_bytes", "rx_bytes"),
                        ("tx_packets", "tx_packets"), ("rx_packets", "rx_packets"),
                        ("tx_flows", "tx_flow_count"), ("rx_flows", "rx_flow_count"),
                    ):
                        point[output] += int(row[column])
                if latest_sample is None or row["last_sample_at"] > latest_sample:
                    latest_sample = row["last_sample_at"]

            protocol_rows = connection.execute(
                "SELECT protocol, direction, SUM(bytes) AS bytes, SUM(packets) AS packets, "
                "SUM(flow_count) AS flows FROM v3_device_traffic_protocol_minutes "
                "WHERE device_id = ? AND bucket_start >= ? AND bucket_start < ? "
                + ("AND protocol = ? " if selected_protocol else "")
                + "GROUP BY protocol, direction ORDER BY protocol, direction",
                ((device_id, start_text, end_text, selected_protocol) if selected_protocol
                 else (device_id, start_text, end_text)),
            ).fetchall()
            protocols: dict[str, dict] = {}
            for row in protocol_rows:
                item = protocols.setdefault(row["protocol"], {
                    "protocol": row["protocol"], "evidence": "network_protocol",
                    "tx_bytes": 0, "rx_bytes": 0,
                    "tx_packets": 0, "rx_packets": 0,
                    "tx_flows": 0, "rx_flows": 0,
                })
                direction = row["direction"]
                item[f"{direction}_bytes"] = int(row["bytes"])
                item[f"{direction}_packets"] = int(row["packets"])
                item[f"{direction}_flows"] = int(row["flows"])
            unassigned = int(connection.execute(
                "SELECT COALESCE(SUM(sample_count), 0) FROM v3_traffic_unassigned_minutes "
                "WHERE bucket_start >= ? AND bucket_start < ?",
                (start_text, end_text),
            ).fetchone()[0])

        series = [grouped[key] for key in sorted(grouped)]
        summary = None
        if series:
            summary = {
                key: sum(point[key] for point in series)
                for key in self._empty_totals()
            }
        return {
            "device_id": device_id,
            "window": {"from": start_text, "to": end_text},
            "resolution": selected_resolution,
            "protocol_filter": selected_protocol,
            "availability": {
                "available": bool(series),
                "reason": None if series else "no_samples",
                "latest_sample_at": latest_sample,
            },
            "freshness": {
                "historical_source": "sqlite_minute_aggregates",
                "latest_sample_at": latest_sample,
            },
            "realtime": self.realtime_window.snapshot(
                device_id, protocol=selected_protocol
            ),
            "summary": summary,
            "series": series,
            "protocols": [protocols[key] for key in sorted(protocols)],
            "data_quality": {
                "unassigned_samples_in_window": unassigned,
                "has_unassigned_or_missing_data": unassigned > 0,
                "note": (
                    "Collection-wide ambiguous or unmapped samples exist in this window; "
                    "they were not guessed onto a device."
                    if unassigned else None
                ),
            },
        }

    def query_peers(
        self,
        device_id: str,
        *,
        start: datetime,
        end: datetime,
        direction: str | None = None,
        protocol: str | None = None,
        sort_by: str = "bytes",
        limit: int = 50,
        offset: int = 0,
        include_peer_ip: bool = True,
    ) -> dict:
        device_id = _identifier(device_id, "device_id")
        start, end, _, _ = self._query_bounds(
            start, end, "hour", TRAFFIC_MAX_QUERY_POINTS
        )
        if direction is not None and direction not in _DIRECTIONS:
            raise TrafficQueryError("invalid_direction", "direction must be tx or rx")
        selected_protocol = _protocol(protocol) if protocol else None
        if sort_by not in {"bytes", "packets"}:
            raise TrafficQueryError("invalid_sort", "sort must be bytes or packets")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise TrafficQueryError("invalid_limit", "limit must be between 1 and 200")
        if type(offset) is not int or offset < 0:
            raise TrafficQueryError("invalid_offset", "offset must be non-negative")
        conditions = ["device_id = ?", "bucket_start >= ?", "bucket_start < ?"]
        parameters: list[object] = [device_id, _iso(start), _iso(end)]
        if direction:
            conditions.append("direction = ?")
            parameters.append(direction)
        if selected_protocol:
            conditions.append("protocol = ?")
            parameters.append(selected_protocol)
        where = " AND ".join(conditions)
        order_column = "total_bytes" if sort_by == "bytes" else "total_packets"
        with self._connection() as connection:
            self._require_device(connection, device_id)
            total = int(connection.execute(
                "SELECT COUNT(*) FROM (SELECT peer_key, direction, protocol FROM "
                "v3_device_traffic_peer_minutes WHERE " + where
                + " GROUP BY peer_key, direction, protocol)", parameters,
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT peer_key, peer_device_id, peer_ip, direction, protocol, "
                "SUM(bytes) AS total_bytes, SUM(packets) AS total_packets, "
                "SUM(flow_count) AS total_flows, MIN(first_sample_at) AS first_seen, "
                "MAX(last_sample_at) AS last_seen FROM v3_device_traffic_peer_minutes WHERE "
                + where + " GROUP BY peer_key, peer_device_id, peer_ip, direction, protocol "
                + f"ORDER BY {order_column} DESC, peer_key, direction, protocol LIMIT ? OFFSET ?",
                (*parameters, limit, offset),
            ).fetchall()
        peers = [{
            "peer_device_id": row["peer_device_id"],
            "peer_ip": row["peer_ip"] if include_peer_ip else None,
            "peer_ip_visible": include_peer_ip,
            "direction": row["direction"],
            "protocol": row["protocol"],
            "bytes": int(row["total_bytes"]),
            "packets": int(row["total_packets"]),
            "flows": int(row["total_flows"]),
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
        } for row in rows]
        return {
            "device_id": device_id,
            "window": {"from": _iso(start), "to": _iso(end)},
            "filters": {"direction": direction, "protocol": selected_protocol},
            "sort": sort_by,
            "availability": {
                "available": bool(peers),
                "reason": None if peers else "no_samples",
            },
            "peers": peers,
            "pagination": {
                "limit": limit, "offset": offset, "total": total,
                "has_more": offset + len(peers) < total,
            },
        }

    def purge_before(self, cutoff: datetime) -> dict[str, int]:
        """Explicit maintenance operation; never called by a read request."""
        cutoff_text = _iso(_utc(cutoff, "cutoff"))
        removed: dict[str, int] = {}
        with self._connection(write=True) as connection:
            for table, column in (
                ("v3_device_traffic_minutes", "bucket_start"),
                ("v3_device_traffic_protocol_minutes", "bucket_start"),
                ("v3_device_traffic_peer_minutes", "bucket_start"),
                ("v3_traffic_unassigned_minutes", "bucket_start"),
                ("v3_traffic_ingest_samples", "occurred_at"),
            ):
                cursor = connection.execute(
                    f"DELETE FROM {table} WHERE {column} < ?", (cutoff_text,)
                )
                removed[table] = cursor.rowcount
            cursor = connection.execute(
                "DELETE FROM v3_traffic_ingest_batches "
                "WHERE COALESCE(last_sample_at, received_at) < ? "
                "AND NOT EXISTS (SELECT 1 FROM v3_traffic_ingest_samples samples "
                "WHERE samples.source_id = v3_traffic_ingest_batches.source_id "
                "AND samples.source_session_id = v3_traffic_ingest_batches.source_session_id "
                "AND samples.batch_id = v3_traffic_ingest_batches.batch_id)",
                (cutoff_text,),
            )
            removed["v3_traffic_ingest_batches"] = cursor.rowcount
        return removed


__all__ = [
    "DeviceTrafficService", "RealtimeTrafficWindow", "TrafficDeviceNotFound",
    "TrafficQueryError", "TrafficSample", "TrafficStoreUnavailable",
    "TrafficValidationError", "ensure_traffic_schema", "record_ip_binding",
    "resolve_ip_binding",
]
