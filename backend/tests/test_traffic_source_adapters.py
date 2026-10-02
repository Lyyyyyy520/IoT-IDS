from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask

import api.probe as probe_api
from runtime_services import BackendServiceContainer, EXTENSION_KEY
from services.device_management import DeviceActor, DeviceManagementService
from services.device_state import DeviceStateService
from services.device_traffic import DeviceTrafficService, RealtimeTrafficWindow
from v3_database import connect_v3_existing, initialize_v3_database


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class FakeIncidentWorkflow:
    def __init__(self):
        self.calls = []

    def create_incident(self, **values):
        self.calls.append(values)
        return {"incident_id": f"incident-{len(self.calls)}"}

def prepare(path: Path):
    initialize_v3_database(path)
    actor = DeviceActor(1, "probe-test", "admin")
    DeviceManagementService(path, clock=lambda: NOW - timedelta(hours=2)).create_device(
        device_id="camera-01", mac="AA:BB:CC:DD:EE:01", display_name="Camera",
        device_type="camera", profile_source="physical", actor=actor,
        request_id="create",
    )
    DeviceStateService(path).record_observation(
        device_id="camera-01", identity_kind="mac",
        identity_value="AA:BB:CC:DD:EE:01", source="test",
        ip_address="192.168.1.10", received_at=NOW - timedelta(hours=1),
    )


def test_versioned_probe_batch_uses_shared_dedup_service(tmp_path, monkeypatch):
    path = tmp_path / "probe-v2.sqlite"
    prepare(path)
    monkeypatch.setenv("IOT_IDS_PROBE_TOKEN", "probe-secret")
    monkeypatch.setattr(probe_api, "execute", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(probe_api, "query_one", lambda *_args, **_kwargs: None)

    app = Flask(__name__)
    container = BackendServiceContainer(path, mqtt_settings_provider=lambda: None)
    container.traffic_service = DeviceTrafficService(
        path, clock=lambda: NOW,
        realtime_window=RealtimeTrafficWindow(clock=lambda: NOW),
    )
    app.extensions[EXTENSION_KEY] = container
    incidents = FakeIncidentWorkflow()
    app.extensions["iot_ids_incident_workflow"] = incidents
    app.register_blueprint(probe_api.probe_bp)
    client = app.test_client()
    payload = {
        "schema_version": 2,
        "source_id": "probe:7",
        "source_session_id": "boot-1",
        "batch_id": "batch-1",
        "batch_sequence": 1,
        "probe_id": 7,
        "probe_name": "Pi-001",
        "alerts": [{
            "risk_level": "high",
            "attack_type": "PortScan",
            "src_ip": "203.0.113.10",
            "dst_ip": "192.168.1.10",
            "src_port": 50000,
            "dst_port": 443,
            "protocol": "TCP",
            "confidence": 0.91,
        }],
        "flows": [{
            "sample_id": "sample-1",
            "occurred_at": (NOW - timedelta(minutes=1)).isoformat(),
            "src_ip": "192.168.1.10",
            "dst_ip": "203.0.113.10",
            "src_port": 12000,
            "dst_port": 443,
            "network_protocol": "TCP",
            "bytes": 50,
            "packets": 1,
            "flow_count": 0,
            "flags": "S",
            "payload": "legacy-only-content",
            "source": "real",
        }],
    }
    headers = {"X-Probe-Token": "probe-secret"}
    first = client.post("/api/probe/push", json=payload, headers=headers)
    second = client.post("/api/probe/push", json=payload, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.get_json()["traffic_aggregation"]["accepted_samples"] == 1
    assert second.get_json()["traffic_aggregation"]["status"] == "duplicate"
    assert first.get_json()["alerts_received"] == 1, first.get_json()
    assert first.get_json()["alert_ingestion"][0]["status"] == "accepted"
    assert second.get_json()["alert_ingestion"][0]["reason_code"] == "duplicate_probe_batch"
    assert len(incidents.calls) == 1
    assert incidents.calls[0]["devices"][0]["device_id"] == "camera-01"
    assert incidents.calls[0]["devices"][0]["user_visible"] is True

    connection = connect_v3_existing(path)
    try:
        aggregate = connection.execute(
            "SELECT tx_bytes FROM v3_device_traffic_minutes WHERE device_id = 'camera-01'"
        ).fetchone()
        sample_columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(v3_traffic_ingest_samples)"
            )
        }
    finally:
        connection.close()
    assert aggregate["tx_bytes"] == 50
    assert "payload" not in sample_columns


def test_legacy_probe_is_explicitly_not_aggregated(tmp_path, monkeypatch):
    path = tmp_path / "probe-legacy.sqlite"
    prepare(path)
    monkeypatch.setenv("IOT_IDS_PROBE_TOKEN", "probe-secret")
    monkeypatch.setattr(probe_api, "execute", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(probe_api, "query_one", lambda *_args, **_kwargs: None)
    app = Flask(__name__)
    container = BackendServiceContainer(path, mqtt_settings_provider=lambda: None)
    container.traffic_service = DeviceTrafficService(
        path, clock=lambda: NOW,
        realtime_window=RealtimeTrafficWindow(clock=lambda: NOW),
    )
    app.extensions[EXTENSION_KEY] = container
    app.register_blueprint(probe_api.probe_bp)
    response = app.test_client().post(
        "/api/probe/push",
        json={"probe_name": "legacy", "alerts": [], "flows": []},
        headers={"X-Probe-Token": "probe-secret"},
    )
    assert response.get_json()["traffic_aggregation"] == {
        "status": "not_ingested",
        "reason_code": "legacy_probe_schema_no_idempotency",
    }


def test_probe_sources_declare_v2_ids_and_no_hardcoded_packet_length():
    root = Path(__file__).resolve().parents[2]
    vm = (root / "edge" / "vm_probe_client.py").read_text(encoding="utf-8")
    pi = (root / "edge" / "probe_client.py").read_text(encoding="utf-8")
    edge_detect = (root / "edge" / "edge_detect.py").read_text(encoding="utf-8")
    for source in (vm, pi, edge_detect):
        assert "'schema_version': 2" in source
        assert "'source_session_id':" in source
        assert "'batch_sequence':" in source
        assert "sample_id" in source
        assert "'occurred_at':" in source
        assert "'network_protocol':" in source
        assert "'flow_count': 0" in source
    assert "'length': 100" not in pi
    assert "length_pat" in pi
    assert "'alerts': []," in edge_detect
    assert "not sent as incident" in edge_detect
    assert "detect_pcap" not in edge_detect
