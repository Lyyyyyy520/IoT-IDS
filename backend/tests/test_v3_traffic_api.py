from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask
import pytest

from api.v3_traffic import create_v3_traffic_blueprint
from services.device_management import DeviceActor, DeviceManagementService
from services.device_state import DeviceStateService
from services.device_traffic import DeviceTrafficService, RealtimeTrafficWindow
from v3_database import initialize_v3_database


NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
ACTOR = DeviceActor(1, "api-admin", "admin")


def app_for(path: Path, service: DeviceTrafficService | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = "traffic-api-test"
    traffic = service or DeviceTrafficService(
        path,
        clock=lambda: NOW,
        realtime_window=RealtimeTrafficWindow(clock=lambda: NOW),
    )
    app.register_blueprint(create_v3_traffic_blueprint(traffic, clock=lambda: NOW))
    return app


def login(client, role: str):
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["username"] = f"test-{role}"
        state["role"] = role


def create_and_bind(path: Path):
    DeviceManagementService(path, clock=lambda: NOW - timedelta(hours=2)).create_device(
        device_id="camera-01",
        mac="AA:BB:CC:DD:EE:01",
        display_name="Camera",
        device_type="camera",
        profile_source="physical",
        actor=ACTOR,
        request_id="create",
    )
    DeviceStateService(path).record_observation(
        device_id="camera-01",
        identity_kind="mac",
        identity_value="AA:BB:CC:DD:EE:01",
        source="test",
        ip_address="192.168.1.10",
        observed_at=NOW - timedelta(hours=1),
        received_at=NOW - timedelta(hours=1),
    )


def row(sample_id: str, minute: int, peer: str, size: int, protocol: str = "TCP"):
    return {
        "sample_id": sample_id,
        "occurred_at": (NOW - timedelta(minutes=minute)).isoformat(),
        "src_ip": "192.168.1.10",
        "dst_ip": peer,
        "network_protocol": protocol,
        "application_protocol": None,
        "application_protocol_inferred": False,
        "src_port": 50000,
        "dst_port": 443,
        "bytes": size,
        "packets": 1,
        "flow_count": 1,
    }


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "traffic-api.sqlite"
    initialize_v3_database(path)
    create_and_bind(path)
    return path


def seed(service: DeviceTrafficService):
    service.ingest_batch(
        source_id="api-probe",
        source_session_id="session-1",
        batch_id="batch-1",
        batch_sequence=1,
        received_at=NOW,
        samples=[
            row("one", 10, "203.0.113.10", 900, "TCP"),
            row("two", 5, "203.0.113.20", 300, "UDP"),
        ],
    )


@pytest.mark.parametrize("endpoint", [
    "/api/v3/devices/camera-01/traffic",
    "/api/v3/devices/camera-01/peers",
])
def test_permissions(endpoint: str, database_path: Path):
    app = app_for(database_path)
    anonymous = app.test_client().get(endpoint)
    assert anonymous.status_code == 401
    assert anonymous.get_json()["error"]["request_id"]

    user = app.test_client()
    login(user, "user")
    assert user.get(endpoint).status_code == 403

    for role in ("admin", "operator"):
        client = app.test_client()
        login(client, role)
        response = client.get(endpoint)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"


def test_empty_data_is_unavailable_not_confirmed_zero(database_path: Path):
    client = app_for(database_path).test_client()
    login(client, "admin")
    payload = client.get(
        "/api/v3/devices/camera-01/traffic?resolution=minute"
    ).get_json()
    assert payload["availability"]["available"] is False
    assert payload["availability"]["reason"] == "no_samples"
    assert payload["summary"] is None
    assert payload["series"] == []
    assert payload["realtime"]["readiness"] == "warming_up"


def test_traffic_resolution_protocol_and_peer_visibility(database_path: Path):
    window = RealtimeTrafficWindow(clock=lambda: NOW)
    service = DeviceTrafficService(database_path, clock=lambda: NOW, realtime_window=window)
    seed(service)
    app = app_for(database_path, service)
    admin = app.test_client()
    login(admin, "admin")
    traffic = admin.get(
        "/api/v3/devices/camera-01/traffic?resolution=5minute&protocol=tcp"
    ).get_json()
    assert traffic["resolution"] == "5minute"
    assert traffic["protocol_filter"] == "TCP"
    assert traffic["summary"]["tx_bytes"] == 900
    assert len(traffic["series"]) == 1
    assert traffic["protocols"][0]["evidence"] == "network_protocol"

    admin_peers = admin.get(
        "/api/v3/devices/camera-01/peers?direction=tx&sort=bytes&limit=1"
    ).get_json()
    assert admin_peers["peers"][0]["peer_ip"] == "203.0.113.10"
    assert admin_peers["pagination"] == {
        "limit": 1, "offset": 0, "total": 2, "has_more": True,
    }

    operator = app.test_client()
    login(operator, "operator")
    operator_peer = operator.get(
        "/api/v3/devices/camera-01/peers?protocol=udp&offset=0"
    ).get_json()["peers"][0]
    assert operator_peer["peer_ip"] is None
    assert operator_peer["peer_ip_visible"] is False
    assert operator_peer["protocol"] == "UDP"


@pytest.mark.parametrize("query, code", [
    ("from=2026-08-01T00:00:00Z&to=2026-09-21T00:00:00Z", "query_range_too_large"),
    ("resolution=second", "invalid_resolution"),
    ("from=2026-09-21T08:00:00&to=2026-09-21T09:00:00Z", "timezone_required"),
])
def test_query_validation(database_path: Path, query: str, code: str):
    client = app_for(database_path).test_client()
    login(client, "admin")
    response = client.get(f"/api/v3/devices/camera-01/traffic?{query}")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == code


def test_device_not_found_and_missing_database_do_not_create(tmp_path: Path):
    database_path = tmp_path / "exists.sqlite"
    initialize_v3_database(database_path)
    client = app_for(database_path).test_client()
    login(client, "admin")
    assert client.get("/api/v3/devices/missing/traffic").status_code == 404

    missing = tmp_path / "must-not-exist.sqlite"
    missing_client = app_for(missing).test_client()
    login(missing_client, "admin")
    response = missing_client.get("/api/v3/devices/camera-01/traffic")
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "database_unavailable"
    assert not missing.exists()
