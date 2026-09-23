from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import UUID

from flask import Flask
import pytest

from api.v3_realtime import create_v3_realtime_blueprint
from services.device_state import DeviceStateService
from services.realtime_events import RealtimeEventStore
from v3_database import initialize_v3_database


NOW = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)
DEVICE_MAC = "AA:BB:CC:DD:EE:02"


def _app(
    database_path: Path,
    *,
    replay_limit: int = 256,
    max_idle_cycles: int = 1,
) -> Flask:
    app = Flask(__name__)
    app.secret_key = "v3-realtime-test-only"
    app.register_blueprint(
        create_v3_realtime_blueprint(
            database_path,
            clock=lambda: NOW,
            waiter=lambda _seconds: None,
            monotonic_clock=lambda: 0.0,
            replay_limit=replay_limit,
            poll_interval=0.001,
            keepalive_interval=0.0,
            max_idle_cycles=max_idle_cycles,
        )
    )
    return app


def _login(client, role: str) -> None:
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["username"] = f"test-{role}"
        state["role"] = role


def _initialized(tmp_path: Path) -> Path:
    database_path = tmp_path / "v3-api.sqlite"
    initialize_v3_database(database_path)
    return database_path


def _append_components(database_path: Path, count: int) -> None:
    store = RealtimeEventStore(database_path)
    for index in range(count):
        store.append(
            event_type="system.component_changed",
            occurred_at=NOW,
            state_version=index + 1,
            payload={"component_id": f"component-{index}"},
        )


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_empty_monitor_is_real_no_store_snapshot(tmp_path, role):
    database_path = _initialized(tmp_path)
    client = _app(database_path).test_client()
    _login(client, role)

    response = client.get("/api/v3/monitor")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    payload = response.get_json()
    assert payload["api_version"] == "v3"
    assert payload["schema_version"] == 4
    assert payload["generated_at"].endswith("Z")
    assert payload["event_cursor"] == 0
    assert payload["devices"] == []
    assert payload["system_components"] == []
    assert payload["capabilities"]["graph"] == {
        "available": False,
        "reason": "graph_snapshots_not_implemented",
    }
    assert payload["capabilities"]["incident"] == {
        "available": True,
        "reason": "recorded_incident_workflow_available",
        "semantics": "no_recorded_incidents_is_not_a_safety_assurance",
    }
    assert payload["incidents"] == {
        "active": [],
        "recent": [],
        "empty_meaning": "no_recorded_incidents_not_proven_safe",
    }


def test_monitor_returns_only_persisted_devices_and_component_health(tmp_path):
    database_path = _initialized(tmp_path)
    service = DeviceStateService(database_path, clock=lambda: NOW)
    service.bind_device(
        device_id="camera-01",
        identity_kind="mac",
        identity_value=DEVICE_MAC,
        display_name="客厅摄像头",
        device_type="camera",
        area_id="building-a",
    )
    service.record_observation(
        device_id="camera-01",
        identity_kind="mac",
        identity_value=DEVICE_MAC,
        source="probe-a",
        ip_address="192.168.4.21",
        sequence=1,
    )
    service.set_component_readiness("mqtt-subscriber", "ready")
    client = _app(database_path).test_client()
    _login(client, "admin")

    payload = client.get("/api/v3/monitor").get_json()

    assert payload["event_cursor"] == 2
    assert payload["devices"] == [
        {
            "device_id": "camera-01",
            "display_name": "客厅摄像头",
            "device_type": "camera",
            "area_id": "building-a",
            "operation_mode": "active",
            "connection_status": "online",
            "ip_address": "192.168.4.21",
            "state_version": 1,
            "observed_at": "2026-09-19T08:00:00Z",
            "received_at": "2026-09-19T08:00:00Z",
            "sources": ["probe-a"],
        }
    ]
    assert payload["system_components"][0]["component_id"] == "mqtt-subscriber"
    assert payload["system_components"][0]["readiness"] == "ready"


@pytest.mark.parametrize("path", ["/api/v3/monitor", "/api/v3/events?after=0"])
def test_v3_auth_fails_closed_with_uniform_error_envelope(tmp_path, path):
    database_path = _initialized(tmp_path)
    client = _app(database_path).test_client()

    anonymous = client.get(path)
    assert anonymous.status_code == 401
    error = anonymous.get_json()["error"]
    assert error["code"] == "unauthenticated"
    UUID(error["request_id"])
    assert anonymous.headers["X-Request-ID"] == error["request_id"]

    _login(client, "user")
    user = client.get(path)
    assert user.status_code == 403
    assert user.get_json()["error"]["code"] == "user_scope_unavailable"


@pytest.mark.parametrize("path", ["/api/v3/monitor", "/api/v3/events?after=0"])
def test_missing_database_returns_503_without_creating_it(tmp_path, path):
    database_path = tmp_path / "absent.sqlite"
    client = _app(database_path).test_client()
    _login(client, "admin")

    response = client.get(path)

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "v3_database_unavailable"
    assert not database_path.exists()


def test_sse_replays_after_cursor_in_order_with_required_headers(tmp_path):
    database_path = _initialized(tmp_path)
    _append_components(database_path, 3)
    client = _app(database_path).test_client()
    _login(client, "operator")

    response = client.get("/api/v3/events?after=1", buffered=True)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["X-Accel-Buffering"] == "no"
    assert body.index("id: 2") < body.index("id: 3")
    assert "id: 1" not in body
    assert "event: system.component_changed" in body
    assert ": keepalive\n\n" in body


def test_last_event_id_takes_precedence_and_survives_app_restart(tmp_path):
    database_path = _initialized(tmp_path)
    _append_components(database_path, 2)

    restarted_client = _app(database_path).test_client()
    _login(restarted_client, "admin")
    response = restarted_client.get(
        "/api/v3/events?after=0",
        headers={"Last-Event-ID": "1"},
        buffered=True,
    )
    body = response.get_data(as_text=True)

    assert "id: 1" not in body
    assert "id: 2" in body


@pytest.mark.parametrize("cursor", ["-1", "1.2", "abc", "+1", ""])
def test_invalid_event_cursor_is_json_400(tmp_path, cursor):
    database_path = _initialized(tmp_path)
    client = _app(database_path).test_client()
    _login(client, "admin")

    response = client.get(f"/api/v3/events?after={cursor}")

    assert response.status_code == 400
    assert response.is_json
    assert response.get_json()["error"]["code"] == "invalid_event_cursor"


def test_replay_limit_emits_snapshot_required_and_closes(tmp_path):
    database_path = _initialized(tmp_path)
    _append_components(database_path, 3)
    client = _app(database_path, replay_limit=2).test_client()
    _login(client, "admin")

    response = client.get("/api/v3/events?after=0", buffered=True)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "event: snapshot.required" in body
    assert '"reason":"replay_limit_exceeded"' in body
    assert "id:" not in body


def test_cursor_before_retained_range_emits_snapshot_required(tmp_path):
    database_path = _initialized(tmp_path)
    _append_components(database_path, 3)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM v3_realtime_events WHERE event_id = 1")
    client = _app(database_path).test_client()
    _login(client, "admin")

    body = client.get("/api/v3/events?after=0", buffered=True).get_data(as_text=True)

    assert "event: snapshot.required" in body
    assert '"reason":"cursor_before_replay_window"' in body


def test_empty_event_stream_emits_comment_keepalive_without_database_event(tmp_path):
    database_path = _initialized(tmp_path)
    client = _app(database_path).test_client()
    _login(client, "operator")

    body = client.get("/api/v3/events?after=0", buffered=True).get_data(as_text=True)

    assert body == ": keepalive\n\n"
    assert RealtimeEventStore(database_path).current_cursor() == 0


def test_nonzero_cursor_against_never_used_log_requires_snapshot(tmp_path):
    database_path = _initialized(tmp_path)
    client = _app(database_path).test_client()
    _login(client, "admin")

    body = client.get("/api/v3/events?after=4", buffered=True).get_data(as_text=True)

    assert "event: snapshot.required" in body
    assert '"reason":"cursor_ahead"' in body
