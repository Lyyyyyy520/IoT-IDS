"""Per-application service container and explicit backend lifecycle."""
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Mapping

from config import MqttSubscriberSettings, mqtt_subscriber_settings
from services.device_state import DeviceStateService
from v3_database import V3_EXPECTED_OBJECTS, V3_MIGRATIONS, read_applied_migrations


LOGGER = logging.getLogger(__name__)
EXTENSION_KEY = "iot_ids_services"
LEGACY_TABLES = frozenset(
    {
        "users",
        "alerts",
        "traffic_logs",
        "audit_logs",
        "assets",
        "policies",
        "config",
        "rules",
    }
)


def _path(value: str | Path | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    return Path(value).expanduser().resolve()


def inspect_database(database_path: str | Path | None) -> dict:
    """Inspect one explicit database read-only and never create or migrate it."""
    path = _path(database_path)
    report = {
        "configured": path is not None,
        "available": False,
        "legacy_schema_ready": False,
        "v3_schema_ready": False,
        "schema_version": 0,
        "reason": None,
    }
    if path is None:
        report["reason"] = "database_path_not_configured"
        return report
    if not path.is_file():
        report["reason"] = "database_file_missing"
        return report

    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("SELECT 1").fetchone()
            objects = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT name, type FROM sqlite_master "
                    "WHERE type IN ('table', 'index')"
                )
            }
            applied = read_applied_migrations(connection)
        finally:
            connection.close()
    except sqlite3.Error:
        report["reason"] = "database_open_failed"
        return report

    report["available"] = True
    try:
        report["legacy_schema_ready"] = LEGACY_TABLES <= {
            name for name, object_type in objects.items() if object_type == "table"
        }
        report["schema_version"] = max(
            (int(row["version"]) for row in applied), default=0
        )
        ledger = {int(row["version"]): row for row in applied}
        migrations_ready = all(
            migration.version in ledger
            and ledger[migration.version]["name"] == migration.name
            and ledger[migration.version]["checksum"] == migration.checksum
            for migration in V3_MIGRATIONS
        )
        objects_ready = all(
            objects.get(name) == object_type
            for name, object_type in V3_EXPECTED_OBJECTS.items()
        )
    except (KeyError, TypeError, ValueError):
        report["reason"] = "database_schema_invalid"
        return report
    report["v3_schema_ready"] = migrations_ready and objects_ready
    if not report["v3_schema_ready"]:
        report["reason"] = "v3_schema_unavailable"
    elif not report["legacy_schema_ready"]:
        report["reason"] = "legacy_schema_unavailable"
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
    capture_service: Any = field(default=None, init=False, repr=False)
    traffic_window: Any = field(default=None, init=False, repr=False)
    traffic_service: Any = field(default=None, init=False, repr=False)
    mqtt_state: str = field(default="stopped", init=False)
    mqtt_reason: str | None = field(default=None, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def database_health(self) -> dict:
        return inspect_database(self.database_path)

    def get_capture_service(self, app):
        with self._lock:
            if self.capture_service is None:
                from services.traffic_capture import TrafficCapture

                self.capture_service = TrafficCapture(
                    application=app,
                    traffic_aggregation_service=self.get_traffic_service(),
                )
            return self.capture_service

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
        capture = container.capture_service
        if capture is not None and bool(getattr(capture, "running", False)):
            try:
                with app.app_context():
                    capture.stop()
            except Exception as exc:
                LOGGER.error(
                    "runtime_service_stop_failed service=capture type=%s",
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
