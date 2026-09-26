from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from flask import Flask
import pytest

from api.v3_devices import create_v3_devices_blueprint
from services.realtime_events import append_realtime_event
from v3_database import (
    V3_DEVICE_LIFECYCLE_MIGRATION,
    V3_MIGRATIONS,
    apply_v3_migrations,
    connect_v3,
    initialize_v3_database,
)


NOW = datetime(2026, 9, 20, 8, 30, tzinfo=timezone.utc)
V1_CHECKSUM = "3fe72003fd5eb35063bd5aea3f677bc66ef0aaaa36cded58a83f46738bd26952"
V2_CHECKSUM = "77ce4e371366c7d3e53640debb212c9a736fd5e9f8849b06e6d3700508d48078"
V3_CHECKSUM = "bfe9842f09d391284b408dd0df36e205e4ad9f92361ee304dd52955c9f6aa329"
V4_CHECKSUM = "685caf41c5d21471c14ef7b6608cce6d43a4cd69fff3961c1888c3166a4bebcd"


def _app(database_path: Path) -> Flask:
    app = Flask(__name__)
    app.secret_key = "v3-devices-test-only"
    app.register_blueprint(
        create_v3_devices_blueprint(database_path, clock=lambda: NOW)
    )
    return app


def _login(client, role: str = "admin") -> str:
    with client.session_transaction() as state:
        state["user_id"] = 7
        state["username"] = f"test-{role}"
        state["role"] = role
    response = client.get("/api/v3/devices")
    return response.headers["X-CSRF-Token"]


def _payload(
    device_id: str = "camera-01",
    mac: str = "AA:BB:CC:DD:EE:01",
    display_name: str = "客厅摄像头",
    **overrides,
) -> dict:
    result = {
        "device_id": device_id,
        "mac": mac,
        "display_name": display_name,
        "device_type": "camera",
        "area_id": "living-room",
        "importance": "high",
        "profile_source": "physical",
    }
    result.update(overrides)
    return result


def _post_device(client, csrf: str, **overrides):
    return client.post(
        "/api/v3/devices",
        json=_payload(**overrides),
        headers={"X-CSRF-Token": csrf},
    )


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "v3-devices.sqlite"
    initialize_v3_database(path)
    return path


def test_v1_v2_v3_checksums_stay_frozen_and_v4_upgrades_idempotently(tmp_path):
    path = tmp_path / "upgrade-v3.sqlite"
    connection = connect_v3(path)
    try:
        apply_v3_migrations(connection, V3_MIGRATIONS[:3])
        connection.execute("CREATE TABLE assets (id INTEGER PRIMARY KEY, mac TEXT)")
        connection.execute("INSERT INTO assets VALUES (1, 'AA:BB:CC:DD:EE:99')")
        connection.execute(
            "INSERT INTO v3_device_profiles "
            "(device_id, identity_kind, identity_value, display_name, device_type, "
            "operation_mode, created_at, updated_at) VALUES "
            "('existing-01', 'mac', 'AA:BB:CC:DD:EE:98', 'Existing', 'sensor', "
            "'active', '2026-09-20T00:00:00Z', '2026-09-20T00:00:00Z')"
        )
        connection.commit()
        first = apply_v3_migrations(connection)
        repeated = apply_v3_migrations(connection)
        migrated = connection.execute(
            "SELECT importance, profile_source, profile_version "
            "FROM v3_device_profiles WHERE device_id = 'existing-01'"
        ).fetchone()
        asset_count = connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        profile_count = connection.execute(
            "SELECT COUNT(*) FROM v3_device_profiles"
        ).fetchone()[0]
    finally:
        connection.close()

    assert [migration.checksum for migration in V3_MIGRATIONS[:3]] == [
        V1_CHECKSUM,
        V2_CHECKSUM,
        V3_CHECKSUM,
    ]
    assert V3_DEVICE_LIFECYCLE_MIGRATION.checksum == V4_CHECKSUM
    assert first["applied_versions"] == [4, 5, 6, 7, 8, 9]
    assert repeated["applied_versions"] == []
    assert repeated["skipped_versions"] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert tuple(migrated) == ("normal", "unclassified", 1)
    assert asset_count == 1
    assert profile_count == 1


def test_admin_full_lifecycle_preserves_connection_and_writes_audit_and_events(database_path):
    client = _app(database_path).test_client()
    csrf = _login(client)

    created = _post_device(client, csrf)
    assert created.status_code == 201
    assert created.get_json()["device"]["connection_status"] == "unknown"
    assert created.get_json()["device"]["profile_version"] == 1

    updated = client.patch(
        "/api/v3/devices/camera-01",
        json={"display_name": "玄关摄像头", "expected_profile_version": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    assert updated.get_json()["device"]["profile_version"] == 2

    mode = client.patch(
        "/api/v3/devices/camera-01",
        json={"operation_mode": "maintenance", "expected_profile_version": 2},
        headers={"X-CSRF-Token": csrf},
    )
    assert mode.status_code == 200
    assert mode.get_json()["device"]["connection_status"] == "unknown"
    assert mode.get_json()["device"]["state_version"] == 0

    retired = client.post(
        "/api/v3/devices/camera-01/retire",
        json={"reason": "设备更换", "expected_profile_version": 3},
        headers={"X-CSRF-Token": csrf},
    )
    assert retired.status_code == 200
    retired_device = retired.get_json()["device"]
    assert retired_device["operation_mode"] == "disabled"
    assert retired_device["lifecycle_status"] == "retired"
    assert retired_device["credential_revocation_required"] is True

    restored = client.post(
        "/api/v3/devices/camera-01/restore",
        json={"expected_profile_version": 4},
        headers={"X-CSRF-Token": csrf},
    )
    assert restored.status_code == 200
    restored_device = restored.get_json()["device"]
    assert restored_device["operation_mode"] == "active"
    assert restored_device["connection_status"] == "unknown"
    assert restored_device["profile_version"] == 5
    assert restored_device["credential_reverification_required"] is True

    with sqlite3.connect(database_path) as connection:
        actions = [
            row[0]
            for row in connection.execute(
                "SELECT action FROM v3_device_management_audit ORDER BY audit_id"
            )
        ]
        events = connection.execute(
            "SELECT event_type, device_id, state_version, payload_json "
            "FROM v3_realtime_events ORDER BY event_id"
        ).fetchall()
    assert actions == [
        "created",
        "updated",
        "operation_mode_changed",
        "retired",
        "restored",
    ]
    assert [row[0] for row in events] == ["device.inventory_changed"] * 5
    assert [row[1] for row in events] == [None] * 5
    assert [row[2] for row in events] == [1, 2, 3, 4, 5]
    assert [json.loads(row[3])["action"] for row in events] == actions


def test_permissions_are_admin_write_operator_read_and_user_forbidden(database_path):
    app = _app(database_path)
    anonymous = app.test_client()
    assert anonymous.get("/api/v3/devices").status_code == 401

    user = app.test_client()
    _login(user, "user")
    assert user.get("/api/v3/devices").status_code == 403

    operator = app.test_client()
    operator_csrf = _login(operator, "operator")
    assert operator.get("/api/v3/devices").status_code == 200
    assert _post_device(operator, operator_csrf).status_code == 403

    admin = app.test_client()
    admin_csrf = _login(admin, "admin")
    assert _post_device(admin, admin_csrf).status_code == 201


def test_duplicate_identity_immutable_fields_and_failed_writes_are_not_recorded(database_path):
    client = _app(database_path).test_client()
    csrf = _login(client)
    assert _post_device(client, csrf).status_code == 201

    duplicate_id = _post_device(
        client, csrf, mac="AA:BB:CC:DD:EE:02", display_name="Duplicate ID"
    )
    duplicate_mac = _post_device(
        client,
        csrf,
        device_id="camera-02",
        display_name="Duplicate MAC",
    )
    immutable = client.patch(
        "/api/v3/devices/camera-01",
        json={"mac": "AA:BB:CC:DD:EE:03", "expected_profile_version": 1},
        headers={"X-CSRF-Token": csrf},
    )

    assert duplicate_id.status_code == 409
    assert duplicate_id.get_json()["error"]["code"] == "device_id_conflict"
    assert duplicate_mac.status_code == 409
    assert duplicate_mac.get_json()["error"]["code"] == "device_identity_conflict"
    assert immutable.status_code == 400
    assert immutable.get_json()["error"]["code"] == "unknown_fields"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_device_management_audit"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_realtime_events"
        ).fetchone()[0] == 1


def test_profile_version_conflict_does_not_overwrite_or_audit(database_path):
    client = _app(database_path).test_client()
    csrf = _login(client)
    _post_device(client, csrf)

    response = client.patch(
        "/api/v3/devices/camera-01",
        json={"display_name": "Stale write", "expected_profile_version": 9},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "profile_version_conflict"
    detail = client.get("/api/v3/devices/camera-01").get_json()["device"]
    assert detail["display_name"] == "客厅摄像头"
    assert detail["profile_version"] == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("device_id", "INVALID DEVICE"),
        ("mac", "AA-not-a-mac-BB"),
        ("display_name", ""),
        ("device_type", "camera/type"),
        ("area_id", "building/one"),
        ("importance", "urgent"),
        ("profile_source", "discovered"),
    ],
)
def test_invalid_create_fields_are_rejected_without_side_effects(
    database_path, field, value
):
    client = _app(database_path).test_client()
    csrf = _login(client)
    response = _post_device(client, csrf, **{field: value})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_device_request"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_device_profiles"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_device_management_audit"
        ).fetchone()[0] == 0


def test_csrf_json_and_size_guards_are_stable(database_path):
    client = _app(database_path).test_client()
    csrf = _login(client)

    missing = client.post("/api/v3/devices", json=_payload())
    wrong = client.post(
        "/api/v3/devices",
        json=_payload(),
        headers={"X-CSRF-Token": "wrong"},
    )
    content_type = client.post(
        "/api/v3/devices",
        data="{}",
        headers={"X-CSRF-Token": csrf, "Content-Type": "text/plain"},
    )
    malformed = client.post(
        "/api/v3/devices",
        data=b"{not-json",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
    )
    oversized = client.post(
        "/api/v3/devices",
        data=b'{' + b'"padding":"' + (b"x" * 17000) + b'"}',
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
    )

    assert missing.status_code == 403
    assert missing.get_json()["error"]["code"] == "csrf_token_missing"
    assert wrong.status_code == 403
    assert wrong.get_json()["error"]["code"] == "csrf_token_invalid"
    assert content_type.status_code == 400
    assert content_type.get_json()["error"]["code"] == "invalid_content_type"
    assert malformed.status_code == 400
    assert malformed.get_json()["error"]["code"] == "invalid_json"
    assert oversized.status_code == 413
    assert oversized.get_json()["error"]["code"] == "request_too_large"
    assert _post_device(client, csrf).status_code == 201


@pytest.mark.parametrize("history_kind", ["observation", "mqtt", "event", "future"])
def test_historical_references_block_permanent_deletion(database_path, history_kind):
    client = _app(database_path).test_client()
    csrf = _login(client)
    _post_device(client, csrf)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        if history_kind == "observation":
            connection.execute(
                "INSERT INTO v3_device_state_observations "
                "(device_id, source, sequence, observed_at, received_at) "
                "VALUES ('camera-01', 'probe', 1, ?, ?)",
                (NOW.isoformat(), NOW.isoformat()),
            )
        elif history_kind == "mqtt":
            connection.execute(
                "INSERT INTO v3_mqtt_boot_sessions VALUES "
                "('camera-01', ?, ?, ?, 1, 100, '1.0.0')",
                ("a" * 32, NOW.isoformat(), NOW.isoformat()),
            )
            connection.execute(
                "INSERT INTO v3_mqtt_device_cursors VALUES ('camera-01', ?, ?)",
                ("a" * 32, NOW.isoformat()),
            )
        elif history_kind == "event":
            append_realtime_event(
                connection,
                event_type="device.telemetry_updated",
                occurred_at=NOW,
                device_id="camera-01",
                state_version=1,
                payload={"source": "test"},
            )
        else:
            connection.execute(
                "CREATE TABLE future_graph_nodes "
                "(node_id INTEGER PRIMARY KEY, device_id TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO future_graph_nodes (device_id) VALUES ('camera-01')"
            )

    response = client.delete(
        "/api/v3/devices/camera-01",
        json={"confirmation": "客厅摄像头"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    body = response.get_json()["error"]
    assert body["code"] == "device_has_history"
    assert body["details"]["blocking_reasons"]
    assert client.get("/api/v3/devices/camera-01").status_code == 200


def test_retirement_preserves_observations(database_path):
    client = _app(database_path).test_client()
    csrf = _login(client)
    _post_device(client, csrf)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO v3_device_state_observations "
            "(device_id, source, sequence, observed_at, received_at) "
            "VALUES ('camera-01', 'probe', 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
    response = client.post(
        "/api/v3/devices/camera-01/retire",
        json={"reason": "retire with history", "expected_profile_version": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_device_state_observations"
        ).fetchone()[0] == 1


def test_clean_mistake_can_be_deleted_but_requires_exact_name_and_keeps_audit(database_path):
    client = _app(database_path).test_client()
    csrf = _login(client)
    _post_device(client, csrf)

    mismatch = client.delete(
        "/api/v3/devices/camera-01",
        json={"confirmation": "客厅摄像头 "},
        headers={"X-CSRF-Token": csrf},
    )
    assert mismatch.status_code == 409
    assert mismatch.get_json()["error"]["code"] == "confirmation_mismatch"

    deleted = client.delete(
        "/api/v3/devices/camera-01",
        json={"confirmation": "客厅摄像头"},
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200
    assert deleted.get_json()["deleted"] is True
    assert client.get("/api/v3/devices/camera-01").status_code == 404
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_device_management_audit "
            "WHERE device_id = 'camera-01'"
        ).fetchone()[0] == 2
        deleted_event = connection.execute(
            "SELECT device_id, payload_json FROM v3_realtime_events "
            "WHERE event_type = 'device.inventory_changed' ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
    assert deleted_event[0] is None
    assert json.loads(deleted_event[1]) == {
        "action": "deleted",
        "device_id": "camera-01",
        "profile_version": 2,
    }


def test_list_search_filters_pagination_and_limit(database_path):
    client = _app(database_path).test_client()
    csrf = _login(client)
    _post_device(client, csrf)
    _post_device(
        client,
        csrf,
        device_id="door-01",
        mac="AA:BB:CC:DD:EE:02",
        display_name="Front Door",
        device_type="door",
        area_id="entry",
        importance="critical",
    )
    _post_device(
        client,
        csrf,
        device_id="sensor-01",
        mac="AA:BB:CC:DD:EE:03",
        display_name="温度传感器",
        device_type="sensor",
        area_id="living-room",
        importance="normal",
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE v3_device_current_state SET connection_status = 'online' "
            "WHERE device_id = 'door-01'"
        )
    retired = client.post(
        "/api/v3/devices/sensor-01/retire",
        json={"reason": "retired", "expected_profile_version": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert retired.status_code == 200

    assert client.get("/api/v3/devices?search=door").get_json()["total"] == 1
    assert client.get(
        "/api/v3/devices?connection_status=online"
    ).get_json()["items"][0]["device_id"] == "door-01"
    assert client.get(
        "/api/v3/devices?operation_mode=disabled&retired=true"
    ).get_json()["items"][0]["device_id"] == "sensor-01"
    assert client.get(
        "/api/v3/devices?area_id=living-room"
    ).get_json()["total"] == 2
    page = client.get("/api/v3/devices?limit=1&offset=1").get_json()
    assert page["items"][0]["device_id"] == "door-01"
    assert page["total"] == 3
    assert client.get("/api/v3/devices?limit=101").status_code == 400


def test_missing_database_returns_503_without_creating_file(tmp_path):
    missing = tmp_path / "must-not-exist.sqlite"
    client = _app(missing).test_client()
    _login(client)

    response = client.get("/api/v3/devices")
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "v3_database_unavailable"
    assert response.get_json()["error"]["request_id"]
    assert not missing.exists()
