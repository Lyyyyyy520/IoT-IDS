"""Per-application service container and explicit backend lifecycle."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sqlite3
import stat
import threading
from typing import Any, Callable, Mapping

from config import MqttSubscriberSettings, mqtt_subscriber_settings
from services.device_state import DeviceStateService
from v3_database import V3_EXPECTED_OBJECTS, V3_MIGRATIONS, read_applied_migrations


LOGGER = logging.getLogger(__name__)
EXTENSION_KEY = "iot_ids_services"
LEGACY_TABLES = frozenset({"users", "audit_logs", "assets", "config"})


def _path(value: str | Path | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    return Path(value).expanduser().resolve()


def _sqlite_failure_reason(error: sqlite3.Error) -> str:
    code = getattr(error, "sqlite_errorcode", None)
    primary = (int(code) & 0xFF) if isinstance(code, int) else None
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
    message = str(error).lower()
    if "locked" in message:
        return "database_locked"
    if "busy" in message:
        return "database_busy"
    if "readonly" in message or "read-only" in message:
        return "database_read_only"
    if "disk is full" in message or "database or disk is full" in message:
        return "database_disk_full"
    if "malformed" in message or "not a database" in message:
        return "database_corrupt"
    return "database_open_failed"


def _database_journal_mode(path: Path) -> str:
    with path.open("rb") as handle:
        header = handle.read(20)
    if len(header) < 20 or header[:16] != b"SQLite format 3\x00":
        return "invalid"
    versions = header[18:20]
    if versions == b"\x01\x01":
        return "rollback"
    if versions == b"\x02\x02":
        return "wal"
    return "unknown"


def inspect_database(database_path: str | Path | None) -> dict:
    """Inspect one explicit SQLite database read-only; never create or migrate."""
    path = _path(database_path)
    report = {
        "configured": path is not None,
        "available": False,
        "legacy_schema_ready": False,
        "v3_schema_ready": False,
        "schema_version": 0,
        "writable": None,
        "reason": None,
    }
    if path is None:
        report["reason"] = "database_path_not_configured"
        return report
    if not path.is_file():
        report["reason"] = "database_file_missing"
        return report
    try:
        mode = path.stat().st_mode
        parent_mode = path.parent.stat().st_mode
        report["writable"] = (
            bool(mode & stat.S_IWRITE)
            and os.access(path, os.W_OK)
            and bool(parent_mode & stat.S_IWRITE)
            and os.access(path.parent, os.W_OK)
        )
        journal_mode = _database_journal_mode(path)
    except OSError:
        report["writable"] = False
        report["reason"] = "database_io_error"
        return report
    if journal_mode == "wal":
        report["reason"] = "database_wal_mode_unsupported"
        return report
    if journal_mode != "rollback":
        report["reason"] = "database_corrupt"
        return report

    connection = None
    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro", uri=True, timeout=0.0
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("SELECT 1").fetchone()
        objects = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name, type FROM sqlite_master "
                "WHERE type IN ('table', 'index')"
            )
        }
        report["available"] = True
        report["legacy_schema_ready"] = LEGACY_TABLES <= {
            name for name, object_type in objects.items() if object_type == "table"
        }
        applied = read_applied_migrations(connection)
    except FileNotFoundError:
        report["reason"] = "database_file_missing"
        return report
    except sqlite3.Error as exc:
        report["reason"] = _sqlite_failure_reason(exc)
        return report
    finally:
        if connection is not None:
            connection.close()

    try:
        ledger = {int(row["version"]): row for row in applied}
        report["schema_version"] = max(ledger, default=0)
        if not ledger:
            report["reason"] = "schema_ledger_missing"
            return report
        if report["schema_version"] > max(m.version for m in V3_MIGRATIONS):
            report["reason"] = "schema_version_unsupported"
            return report
        checksum_mismatch = any(
            migration.version in ledger
            and (
                ledger[migration.version]["name"] != migration.name
                or ledger[migration.version]["checksum"] != migration.checksum
            )
            for migration in V3_MIGRATIONS
        )
        if checksum_mismatch:
            report["reason"] = "schema_checksum_mismatch"
            return report
        migrations_ready = all(migration.version in ledger for migration in V3_MIGRATIONS)
        objects_ready = all(
            objects.get(name) == object_type
            for name, object_type in V3_EXPECTED_OBJECTS.items()
        )
        report["v3_schema_ready"] = migrations_ready and objects_ready
    except (KeyError, TypeError, ValueError):
        report["reason"] = "schema_invalid"
        return report

    if not report["v3_schema_ready"]:
        report["reason"] = "schema_incomplete"
    elif not report["legacy_schema_ready"]:
        report["reason"] = "legacy_schema_unavailable"
    elif report["writable"] is False:
        report["reason"] = "database_read_only"
    return report


def _default_mqtt_factory(
    state_service: DeviceStateService,
    settings: MqttSubscriberSettings,
    environment: Mapping[str, str],
):
    from services.mqtt_subscriber import ManagedMqttHeartbeatSubscriber
    from services.device_discovery import DeviceDiscoveryService
    from services.mqtt_ingestion import MqttHeartbeatIngestor

    return ManagedMqttHeartbeatSubscriber(
        state_service,
        ingestor=MqttHeartbeatIngestor(
            state_service,
            discovery_service=DeviceDiscoveryService(state_service.database_path),
        ),
        settings_provider=lambda: settings,
        environment=environment,
    )


@dataclass
class BackendServiceContainer:
    """Mutable runtime state owned by exactly one Flask application."""

    database_path: Path | None
    mqtt_settings_provider: Callable[[], MqttSubscriberSettings]
    traffic_clock: Callable[[], Any] | None = None
    mqtt_subscriber_factory: Callable[
        [DeviceStateService, MqttSubscriberSettings, Mapping[str, str]], Any
    ] = _default_mqtt_factory
    environment: Mapping[str, str] = field(default_factory=lambda: os.environ, repr=False)
    mqtt_subscriber: Any = field(default=None, init=False, repr=False)
    traffic_window: Any = field(default=None, init=False, repr=False)
    traffic_service: Any = field(default=None, init=False, repr=False)
    mqtt_state: str = field(default="stopped", init=False)
    mqtt_reason: str | None = field(default=None, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    degraded_reasons: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    runtime_started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc), init=False, repr=False
    )

    def mark_degraded(self, component: str, reason: str) -> None:
        with self._lock:
            self.degraded_reasons[component] = reason

    def clear_degraded(self, *components: str) -> None:
        with self._lock:
            for component in components:
                self.degraded_reasons.pop(component, None)

    def database_health(self) -> dict:
        return inspect_database(self.database_path)

    def get_traffic_window(self):
        with self._lock:
            if self.traffic_window is None:
                from services.device_traffic import RealtimeTrafficWindow

                self.traffic_window = RealtimeTrafficWindow(clock=self.traffic_clock)
            return self.traffic_window

    def get_traffic_service(self):
        with self._lock:
            if self.traffic_service is None:
                from services.device_traffic import DeviceTrafficService

                self.traffic_service = DeviceTrafficService(
                    self.database_path,
                    realtime_window=self.get_traffic_window(),
                    clock=self.traffic_clock,
                )
            return self.traffic_service


def get_service_container(app) -> BackendServiceContainer:
    container = app.extensions.get(EXTENSION_KEY)
    if not isinstance(container, BackendServiceContainer):
        raise RuntimeError("IoT IDS service container is not configured")
    return container


def _is_debug_reloader_parent(app, environment: Mapping[str, str]) -> bool:
    if not bool(app.debug):
        return False
    child = environment.get("WERKZEUG_RUN_MAIN", "").strip().lower()
    return child not in {"1", "true"}


def start_runtime_services(app) -> dict:
    """Explicitly start enabled runtime services; safe to call repeatedly."""
    container = get_service_container(app)
    with container._lock:
        if container.mqtt_subscriber is not None:
            return {"mqtt": container.mqtt_state, "reason": container.mqtt_reason}
        if _is_debug_reloader_parent(app, container.environment):
            container.mqtt_state = "skipped"
            container.mqtt_reason = "debug_reloader_parent"
            return {"mqtt": container.mqtt_state, "reason": container.mqtt_reason}
        worker_count = str(container.environment.get("WEB_CONCURRENCY", "1")).strip()
        multi_worker_setting = (
            bool(worker_count)
            and (not worker_count.isdecimal() or int(worker_count) > 1)
        )
        wsgi_worker = any(
            str(container.environment.get(key, "")).strip()
            for key in ("GUNICORN_CMD_ARGS", "GUNICORN_WORKER_ID", "UWSGI_ORIGINAL_PROC_NAME")
        ) or multi_worker_setting
        if wsgi_worker:
            container.mqtt_state = "skipped"
            container.mqtt_reason = "mqtt_managed_by_single_worker"
            return {"mqtt": container.mqtt_state, "reason": container.mqtt_reason}
        try:
            settings = container.mqtt_settings_provider()
            if not isinstance(settings, MqttSubscriberSettings):
                raise TypeError("invalid MQTT settings object")
            settings.validate()
        except Exception as exc:
            LOGGER.error(
                "runtime_service_start_failed service=mqtt code=configuration_error type=%s",
                type(exc).__name__,
            )
            container.mqtt_state = "failed"
            container.mqtt_reason = "configuration_error"
            return {"mqtt": container.mqtt_state, "reason": container.mqtt_reason}
        if not settings.enabled:
            container.mqtt_state = "disabled"
            container.mqtt_reason = None
            return {"mqtt": container.mqtt_state, "reason": None}

        database_health = container.database_health()
        if not database_health["available"]:
            container.mqtt_state = "failed"
            container.mqtt_reason = database_health["reason"]
            return {"mqtt": container.mqtt_state, "reason": container.mqtt_reason}
        if not database_health["v3_schema_ready"]:
            container.mqtt_state = "failed"
            container.mqtt_reason = "v3_schema_unavailable"
            return {"mqtt": container.mqtt_state, "reason": container.mqtt_reason}
        if database_health.get("writable") is False:
            container.mqtt_state = "failed"
            container.mqtt_reason = "database_read_only"
            return {"mqtt": container.mqtt_state, "reason": container.mqtt_reason}

        state_service = DeviceStateService(
            container.database_path,
            create_if_missing=False,
        )
        subscriber = None
        try:
            subscriber = container.mqtt_subscriber_factory(
                state_service,
                settings,
                container.environment,
            )
            if not subscriber.start():
                raise RuntimeError("subscriber refused to start")
        except Exception as exc:
            LOGGER.error(
                "runtime_service_start_failed service=mqtt code=start_failed type=%s",
                type(exc).__name__,
            )
            if subscriber is not None:
                try:
                    subscriber.stop()
                except Exception as stop_exc:
                    LOGGER.error(
                        "runtime_service_stop_failed service=mqtt type=%s",
                        type(stop_exc).__name__,
                    )
            container.mqtt_subscriber = None
            container.mqtt_state = "failed"
            container.mqtt_reason = "start_failed"
            return {"mqtt": container.mqtt_state, "reason": container.mqtt_reason}

        container.mqtt_subscriber = subscriber
        container.mqtt_state = "running"
        container.mqtt_reason = None
        return {"mqtt": "running", "reason": None}


def stop_runtime_services(app) -> dict:
    """Stop app-owned background services; repeated calls are harmless."""
    container = get_service_container(app)
    with container._lock:
        subscriber = container.mqtt_subscriber
        container.mqtt_subscriber = None
        if subscriber is not None:
            try:
                subscriber.stop()
            except Exception as exc:
                LOGGER.error(
                    "runtime_service_stop_failed service=mqtt type=%s",
                    type(exc).__name__,
                )
        container.mqtt_state = "stopped"
        container.mqtt_reason = None
        return {"mqtt": "stopped", "reason": None}


def default_mqtt_settings_provider() -> MqttSubscriberSettings:
    return mqtt_subscriber_settings()


__all__ = [
    "BackendServiceContainer",
    "EXTENSION_KEY",
    "default_mqtt_settings_provider",
    "get_service_container",
    "inspect_database",
    "start_runtime_services",
    "stop_runtime_services",
]
