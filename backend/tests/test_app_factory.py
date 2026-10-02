from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
import threading

import pytest

import app as app_module
from app import create_app
from config import MqttSubscriberSettings
from database import init_db
from runtime_services import (
    EXTENSION_KEY,
    get_service_container,
    start_runtime_services,
    stop_runtime_services,
)
from v3_database import V3_MIGRATIONS, apply_v3_migrations, connect_v3, initialize_v3_database
from v3_db_maintenance import RetentionConfigurationError


V1_CHECKSUM = "3fe72003fd5eb35063bd5aea3f677bc66ef0aaaa36cded58a83f46738bd26952"
V2_CHECKSUM = "77ce4e371366c7d3e53640debb212c9a736fd5e9f8849b06e6d3700508d48078"
V3_CHECKSUM = "bfe9842f09d391284b408dd0df36e205e4ad9f92361ee304dd52955c9f6aa329"
V4_CHECKSUM = "685caf41c5d21471c14ef7b6608cce6d43a4cd69fff3961c1888c3166a4bebcd"
V5_CHECKSUM = "77e7011d94ef2b3a7022013fbef0c2e68f70f1ca9ead58446dd3d4bde78c74c6"
V6_CHECKSUM = "f1ce25c5393381750c7582eb7dc783c7625db80a0dad483c1e46cf1b7521b61d"
V7_CHECKSUM = "5e8e572496607b58d0ccf93be0bcd1deaaa7d3935f93cef54cccd35e905b3623"
V8_CHECKSUM = "b87d02359023eafef439bbf04ce0f9c4929e73bd03a08f3dff955c7f1306d3ac"
V9_CHECKSUM = "cbd47c1e4e2807b770b7de3e5e532ef40af34d931bf417565f62254e57e363f2"


def _settings(enabled=True):
    return MqttSubscriberSettings(
        enabled=enabled,
        host="mqtt.test.invalid" if enabled else "",
        port=1883,
        username="backend-reader" if enabled else "",
        password="test-only-password" if enabled else "",
        client_id="backend-test",
        keepalive=30,
        qos=1,
        tls_enabled=False,
        queue_size=8,
        reconnect_min_seconds=1,
        reconnect_max_seconds=8,
        reconnect_jitter_ratio=0.1,
    )


def _config(database_path, **overrides):
    result = {
        "TESTING": True,
        "SECRET_KEY": "factory-test-secret",
        "DATABASE_PATH": str(database_path) if database_path is not None else None,
        "V3_CLOCK": lambda: datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc),
        "V3_WAITER": lambda _seconds: None,
        "V3_MONOTONIC_CLOCK": lambda: 0.0,
        "V3_KEEPALIVE_INTERVAL": 0.0,
        "V3_POLL_INTERVAL": 0.001,
        "V3_MAX_IDLE_CYCLES": 1,
    }
    result.update(overrides)
    return result


def _migrated_database(tmp_path):
    database_path = tmp_path / "runtime.sqlite"
    init_db(database_path)
    initialize_v3_database(database_path)
    return database_path


def _login(client, role):
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["username"] = f"test-{role}"
        state["role"] = role


def test_create_app_has_no_database_or_thread_side_effect(tmp_path):
    missing = tmp_path / "missing.sqlite"
    before = {(thread.name, thread.ident) for thread in threading.enumerate()}
    provider_calls = []
    factory_calls = []

    application = create_app(
        _config(missing),
        mqtt_settings_provider=lambda: provider_calls.append(True),
        mqtt_subscriber_factory=lambda *_args: factory_calls.append(True),
    )
    application.test_client().get("/api/health")

    assert application.extensions[EXTENSION_KEY] is not None
    assert not missing.exists()
    assert provider_calls == []
    assert factory_calls == []
    assert {(thread.name, thread.ident) for thread in threading.enumerate()} == before


def test_main_explicitly_validates_starts_runs_and_stops(monkeypatch):
    calls = []

    class FakeApplication:
        debug = False

        def run(self, **options):
            calls.append(("run", options))

    application = FakeApplication()
    monkeypatch.setattr(app_module, "create_app", lambda: application)
    monkeypatch.setattr(
        app_module,
        "validate_runtime_configuration",
        lambda selected: calls.append(("validate", selected)),
    )
    monkeypatch.setattr(
        app_module,
        "start_runtime_services",
        lambda selected: calls.append(("start", selected)),
    )
    monkeypatch.setattr(
        app_module,
        "stop_runtime_services",
        lambda selected: calls.append(("stop", selected)),
    )

    app_module.main()

    assert [call[0] for call in calls] == ["validate", "start", "run", "stop"]
    assert calls[2][1]["use_reloader"] is False


def test_multiple_apps_have_independent_services_and_identical_routes(tmp_path):
    first = create_app(_config(tmp_path / "first.sqlite"))
    second = create_app(_config(tmp_path / "second.sqlite"))

    assert get_service_container(first) is not get_service_container(second)
    assert get_service_container(first).database_path != get_service_container(second).database_path
    assert {rule.rule for rule in first.url_map.iter_rules()} == {
        rule.rule for rule in second.url_map.iter_rules()
    }
    assert set(first.blueprints) == {
        "legacy_api",
        "probe",
        "v3_devices",
        "v3_device_discovery",
        "v3_realtime",
        "v3_traffic",
        "v3_mobile",
        "v3_incidents",
        "v3_system_health",
    }


def test_missing_database_health_and_all_database_routes_fail_closed(tmp_path):
    missing = tmp_path / "missing.sqlite"
    client = create_app(_config(missing)).test_client()

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.get_json()["status"] == "degraded"
    assert health.get_json()["database"]["available"] is False

    _login(client, "admin")
    assert client.get("/api/v3/monitor").status_code == 503
    assert client.get("/api/v3/events?after=0").status_code == 503
    assert client.get("/api/v3/devices").status_code == 503
    assert client.get("/api/assets").status_code == 404
    assert not missing.exists()


def test_unconfigured_database_path_keeps_health_available_and_v3_closed():
    client = create_app(_config(None)).test_client()

    health = client.get("/api/health").get_json()
    assert health["status"] == "degraded"
    assert health["database"]["configured"] is False
    assert health["database"]["reason"] == "database_path_not_configured"

    _login(client, "admin")
    assert client.get("/api/v3/monitor").status_code == 503


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_migrated_database_exposes_registered_v3_routes(tmp_path, role):
    database_path = _migrated_database(tmp_path)
    client = create_app(_config(database_path)).test_client()
    _login(client, role)

    health = client.get("/api/health")
    monitor = client.get("/api/v3/monitor")
    events = client.get("/api/v3/events?after=0", buffered=True)
    devices = client.get("/api/v3/devices")
    discovered = client.get("/api/v3/devices/discovered")

    assert health.get_json()["status"] == "ok"
    assert health.get_json()["database"]["v3_schema_ready"] is True
    assert monitor.status_code == 200
    assert monitor.get_json()["devices"] == []
    assert events.status_code == 200
    assert events.mimetype == "text/event-stream"
    assert devices.status_code == 200
    assert devices.get_json()["items"] == []
    assert discovered.status_code == 200
    assert discovered.get_json()["items"] == []


@pytest.mark.parametrize(
    ("role", "expected"),
    [(None, 401), ("user", 403)],
)
def test_registered_v3_routes_preserve_restricted_roles(tmp_path, role, expected):
    database_path = _migrated_database(tmp_path)
    client = create_app(_config(database_path)).test_client()
    if role:
        _login(client, role)

    assert client.get("/api/v3/monitor").status_code == expected
    assert client.get("/api/v3/events?after=0").status_code == expected
    assert client.get("/api/v3/devices").status_code == expected
    assert client.get("/api/v3/devices/discovered").status_code == expected


def test_current_and_compatibility_routes_remain_registered(tmp_path):
    application = create_app(_config(tmp_path / "missing.sqlite"))
    rules = {rule.rule for rule in application.url_map.iter_rules()}

    assert {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/me"} <= rules
    assert {
        "/api/probe/register", "/api/probe/heartbeat", "/api/probe/push",
        "/api/probe/control", "/api/probe/control-status", "/api/probe/status-report",
    } <= rules
    assert {
        "/api/v3/monitor", "/api/v3/events", "/api/v3/devices",
        "/api/v3/devices/<device_id>", "/api/v3/devices/discovered",
        "/api/v3/devices/discovered/<candidate_id>", "/api/v3/devices/<device_id>/traffic",
        "/api/v3/incidents", "/api/v3/system/health", "/api/v3/mobile-users",
    } <= rules
    assert not {
        "/api/dashboard/stats", "/api/alerts", "/api/assets", "/api/traffic/summary",
        "/api/analysis/mitre", "/api/detect/upload", "/api/export/excel",
        "/api/policy", "/api/logs", "/api/probe/list", "/api/probe/status",
        "/api/capture/start",
    } & rules


def test_create_app_does_not_create_default_account(tmp_path, monkeypatch):
    monkeypatch.delenv("IOT_IDS_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    database_path = _migrated_database(tmp_path)
    with sqlite3.connect(database_path) as connection:
        before = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    create_app(_config(database_path))

    with sqlite3.connect(database_path) as connection:
        after = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert before == after == 0


@dataclass
class FakeSubscriber:
    start_calls: int = 0
    stop_calls: int = 0

    def start(self):
        self.start_calls += 1
        return True

    def stop(self):
        self.stop_calls += 1


def test_explicit_runtime_start_and_stop_are_idempotent(tmp_path):
    database_path = _migrated_database(tmp_path)
    created = []

    def factory(_state_service, _settings, _environment):
        subscriber = FakeSubscriber()
        created.append(subscriber)
        return subscriber

    application = create_app(
        _config(database_path),
        mqtt_settings_provider=lambda: _settings(),
        mqtt_subscriber_factory=factory,
        service_environment={},
    )

    assert start_runtime_services(application)["mqtt"] == "running"
    assert start_runtime_services(application)["mqtt"] == "running"
    assert len(created) == 1
    assert created[0].start_calls == 1
    assert stop_runtime_services(application)["mqtt"] == "stopped"
    assert stop_runtime_services(application)["mqtt"] == "stopped"
    assert created[0].stop_calls == 1


def test_default_disabled_runtime_does_not_load_paho(tmp_path, monkeypatch):
    imported = []
    monkeypatch.setattr(
        "importlib.import_module",
        lambda name, *args, **kwargs: imported.append(name),
    )
    application = create_app(
        _config(tmp_path / "missing.sqlite"),
        mqtt_settings_provider=lambda: _settings(enabled=False),
    )

    assert start_runtime_services(application)["mqtt"] == "disabled"
    assert "paho.mqtt.client" not in imported


def test_debug_reloader_parent_does_not_construct_subscriber(tmp_path):
    database_path = _migrated_database(tmp_path)
    factory_calls = []
    application = create_app(
        _config(database_path, DEBUG=True),
        mqtt_settings_provider=lambda: _settings(),
        mqtt_subscriber_factory=lambda *_args: factory_calls.append(True),
        service_environment={},
    )

    result = start_runtime_services(application)

    assert result == {"mqtt": "skipped", "reason": "debug_reloader_parent"}
    assert factory_calls == []


@pytest.mark.parametrize("schema_versions", [None, 2])
def test_mqtt_does_not_start_without_database_or_complete_v3_schema(
    tmp_path, schema_versions
):
    database_path = tmp_path / "runtime.sqlite"
    if schema_versions is not None:
        init_db(database_path)
        connection = connect_v3(database_path)
        try:
            apply_v3_migrations(connection, V3_MIGRATIONS[:schema_versions])
        finally:
            connection.close()
    factory_calls = []
    application = create_app(
        _config(database_path),
        mqtt_settings_provider=lambda: _settings(),
        mqtt_subscriber_factory=lambda *_args: factory_calls.append(True),
        service_environment={},
    )

    result = start_runtime_services(application)

    assert result["mqtt"] == "failed"
    assert factory_calls == []
    assert not database_path.exists() if schema_versions is None else database_path.exists()


def test_runtime_services_refuse_read_only_database(tmp_path, monkeypatch):
    database_path = _migrated_database(tmp_path)
    monkeypatch.setattr("runtime_services.os.access", lambda *_args: False)
    application = create_app(
        _config(database_path),
        mqtt_settings_provider=lambda: _settings(),
        mqtt_subscriber_factory=lambda *_args: pytest.fail("must not create subscriber"),
        service_environment={},
    )

    result = start_runtime_services(application)

    assert result == {"mqtt": "failed", "reason": "database_read_only"}


def test_failed_runtime_start_stops_partial_subscriber_thread(tmp_path):
    database_path = _migrated_database(tmp_path)
    stopped = threading.Event()
    worker_done = threading.Event()

    class PartialSubscriber:
        def start(self):
            self.worker = threading.Thread(
                target=lambda: (stopped.wait(timeout=2), worker_done.set()),
                name="partial-mqtt-test-worker",
                daemon=True,
            )
            self.worker.start()
            return False

        def stop(self):
            stopped.set()
            self.worker.join(timeout=1)

    application = create_app(
        _config(database_path),
        mqtt_settings_provider=lambda: _settings(),
        mqtt_subscriber_factory=lambda *_args: PartialSubscriber(),
        service_environment={},
    )

    result = start_runtime_services(application)

    assert result == {"mqtt": "failed", "reason": "start_failed"}
    assert worker_done.wait(timeout=1)
    assert not any(
        thread.name == "partial-mqtt-test-worker" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_migration_checksums_are_unchanged():
    assert [migration.checksum for migration in V3_MIGRATIONS[:8]] == [
        V1_CHECKSUM,
        V2_CHECKSUM,
        V3_CHECKSUM,
        V4_CHECKSUM,
        V5_CHECKSUM,
        V6_CHECKSUM,
        V7_CHECKSUM,
        V8_CHECKSUM,
    ]
    assert V3_MIGRATIONS[8].checksum == V9_CHECKSUM


def test_multiworker_wsgi_does_not_start_per_process_mqtt_subscriber(tmp_path):
    factory_calls = []
    application = create_app(
        _config(tmp_path / "missing.sqlite"),
        mqtt_settings_provider=lambda: _settings(),
        mqtt_subscriber_factory=lambda *_args: factory_calls.append(True),
        service_environment={"WEB_CONCURRENCY": "4"},
    )

    result = start_runtime_services(application)

    assert result == {"mqtt": "skipped", "reason": "mqtt_managed_by_single_worker"}
    assert factory_calls == []


def test_runtime_container_has_no_legacy_capture_worker(tmp_path):
    application = create_app(
        _config(tmp_path / "missing.sqlite"),
        mqtt_settings_provider=lambda: _settings(enabled=False),
        service_environment={},
    )
    container = get_service_container(application)
    assert not hasattr(container, "capture_service")
    assert not hasattr(container, "get_capture_service")

def test_retention_environment_is_validated_at_application_startup(tmp_path, monkeypatch):
    database_path = tmp_path / "must-not-be-created.sqlite"
    monkeypatch.setenv("IOT_IDS_RETENTION_AUDIT_RECORDS_DAYS", "0")

    with pytest.raises(RetentionConfigurationError) as error:
        create_app(_config(database_path))

    assert error.value.code == "retention_days_out_of_range"
    assert not database_path.exists()
