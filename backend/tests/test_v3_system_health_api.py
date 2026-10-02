from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3

from app import create_app
from config import MqttSubscriberSettings
from database import init_db
from v3_database import initialize_v3_database
from v3_db_maintenance import apply_retention


OBSERVED = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
SECRET_HASH = "f" * 64
PRIVATE_MAC = "aa:bb:cc:dd:ee:30"
PRIVATE_IP = "192.0.2.30"
PRIVATE_TEXT = "private-health-payload"


def _app(path):
    settings = MqttSubscriberSettings(enabled=False)
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "system-health-test-secret",
            "DATABASE_PATH": str(path),
            "V3_CLOCK": lambda: OBSERVED,
        },
        mqtt_settings_provider=lambda: settings,
        service_environment={},
    )


def _login(client, role):
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["username"] = "test-" + role
        state["role"] = role


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_health_api_permissions_redaction_and_read_only_behavior(tmp_path):
    path = tmp_path / "health-isolated.sqlite"
    init_db(path)
    initialize_v3_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO v3_device_profiles(device_id,identity_kind,identity_value,"
            "display_name,device_type,created_at,updated_at) "
            "VALUES('health-device','mac',?,'Private profile','camera',?,?)",
            (PRIVATE_MAC, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO v3_realtime_events(event_type,occurred_at,device_id,payload_json) "
            "VALUES('device.telemetry_updated',?,'health-device',?)",
            (OBSERVED.isoformat(), json.dumps({
                "ip_address": PRIVATE_IP,
                "token_hash": SECRET_HASH,
                "private": PRIVATE_TEXT,
            })),
        )
        connection.execute(
            "INSERT INTO v3_system_component_health(component_id,readiness,started_at,"
            "ready_at,reason,updated_at) VALUES('device_discovery','degraded',?,?,?,?)",
            ("2026-01-01T00:00:00Z", None,
             "raw-private-value-" + SECRET_HASH, "2026-01-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO v3_system_component_health(component_id,readiness,started_at,"
            "ready_at,reason,updated_at) VALUES('database_maintenance','ready',?,?,?,?)",
            ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
             PRIVATE_TEXT, PRIVATE_TEXT),
        )

    client = _app(path).test_client()
    before = _digest(path)

    assert client.get("/api/v3/system/health").status_code == 401
    assert client.get(
        "/api/v3/system/health", headers={"Authorization": "Bearer mobile-test"}
    ).status_code == 403

    _login(client, "user")
    assert client.get("/api/v3/system/health").status_code == 403

    for role in ("admin", "operator"):
        _login(client, role)
        response = client.get("/api/v3/system/health")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["automatic_maintenance"] is False
        assert payload["observed_at"] == OBSERVED.isoformat().replace("+00:00", "Z")
        assert payload["components"]["api"]["status"] == "ready"
        assert payload["components"]["database"]["status"] == "ready"
        assert payload["components"]["database"]["exists"] is True
        assert payload["components"]["database"]["readable"] is True
        assert payload["components"]["database"]["writable"] is True
        assert payload["components"]["schema"]["status"] == "ready"
        assert payload["components"]["schema"]["migration_complete"] is True
        assert payload["components"]["schema"]["migration_checksums_valid"] is True
        assert payload["components"]["integrity_check"]["result"] == "ok"
        assert payload["components"]["event_log"]["retained_events"] == 1
        assert payload["components"]["mqtt"]["status"] == "unavailable"
        assert payload["components"]["graph"]["status"] == "unavailable"
        assert payload["components"]["graph"]["reason_code"] == "graph_capability_unavailable"
        assert payload["maintenance"]["last_successful_at"] is None
        assert payload["maintenance"]["last_apply_at"] is None
        assert payload["maintenance"]["last_plan_at"] is None
        assert payload["maintenance"]["last_plan_reason_code"] == "maintenance_plan_read_only"
        serialized = json.dumps(payload)
        for private in (PRIVATE_MAC, PRIVATE_IP, PRIVATE_TEXT, SECRET_HASH, str(path.resolve())):
            assert private not in serialized

    assert _digest(path) == before
    assert not list(tmp_path.glob(path.name + "-*"))


def test_health_reports_missing_database_without_creating_it(tmp_path):
    path = tmp_path / "missing-health.sqlite"
    client = _app(path).test_client()
    _login(client, "admin")

    response = client.get("/api/v3/system/health")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["components"]["database"]["status"] == "unavailable"
    assert payload["components"]["database"]["reason_code"] == "database_file_missing"
    assert payload["components"]["schema"]["status"] == "unavailable"
    assert payload["components"]["integrity_check"]["result"] == "unavailable"
    assert not path.exists()


def test_health_refuses_wal_database_without_touching_sidecars(tmp_path):
    path = tmp_path / "health-wal.sqlite"
    init_db(path)
    initialize_v3_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
    sidecars = [Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    assert not any(item.exists() for item in sidecars)

    client = _app(path).test_client()
    _login(client, "operator")
    response = client.get("/api/v3/system/health")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["components"]["database"]["status"] == "degraded"
    assert payload["components"]["database"]["reason_code"] == "database_wal_mode_unsupported"
    assert payload["components"]["integrity_check"]["result"] == "unavailable"
    assert not any(item.exists() for item in sidecars)


def test_health_reports_locked_database_with_stable_reason_code(tmp_path):
    path = tmp_path / "health-locked.sqlite"
    init_db(path)
    initialize_v3_database(path)
    writer = sqlite3.connect(path, timeout=0)
    writer.execute("BEGIN EXCLUSIVE")
    client = _app(path).test_client()
    _login(client, "admin")

    try:
        response = client.get("/api/v3/system/health")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["components"]["database"]["status"] == "degraded"
        assert payload["components"]["database"]["reason_code"] in {
            "database_locked", "database_busy"
        }
    finally:
        writer.rollback()
        writer.close()


def test_health_reports_last_apply_without_mutation_or_path_leak(tmp_path):
    path = tmp_path / "health-after-maintenance.sqlite"
    init_db(path)
    initialize_v3_database(path)
    backup_directory = tmp_path / "verified-backups"
    backup_directory.mkdir()
    apply_retention(path, backup_directory, now=OBSERVED)
    before = _digest(path)

    client = _app(path).test_client()
    _login(client, "admin")
    payload = client.get("/api/v3/system/health").get_json()

    assert payload["maintenance"]["last_apply_at"] == OBSERVED.isoformat().replace("+00:00", "Z")
    assert payload["maintenance"]["last_plan_at"] is None
    assert payload["maintenance"]["last_plan_reason_code"] == "maintenance_plan_read_only"
    serialized = json.dumps(payload)
    assert str(path.resolve()) not in serialized
    assert _digest(path) == before
