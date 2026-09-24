from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from flask import Flask
import pytest

from api.v3_device_discovery import create_v3_device_discovery_blueprint
from api.v3_devices import create_v3_devices_blueprint
from services.device_discovery import (
    CandidateIdentityConflictError,
    CandidateVersionConflictError,
    DeviceDiscoveryError,
    DeviceDiscoveryService,
)
from services.device_management import DeviceActor, DeviceIdConflictError, DeviceManagementService
from services.device_state import DeviceStateService
from services.mqtt_ingestion import MqttHeartbeatIngestor
from v3_db_upgrade import plan_database
from v3_database import V3_MIGRATIONS, apply_v3_migrations, connect_v3, initialize_v3_database


NOW = datetime(2026, 9, 24, 4, 0, tzinfo=timezone.utc)
ADMIN = DeviceActor(1, "discovery-admin", "admin")
MAC_A = "AA:BB:CC:DD:EE:71"
MAC_B = "AA:BB:CC:DD:EE:72"
BOOT = "ab" * 16
FROZEN_V1_TO_V8 = [
    "3fe72003fd5eb35063bd5aea3f677bc66ef0aaaa36cded58a83f46738bd26952",
    "77ce4e371366c7d3e53640debb212c9a736fd5e9f8849b06e6d3700508d48078",
    "bfe9842f09d391284b408dd0df36e205e4ad9f92361ee304dd52955c9f6aa329",
    "685caf41c5d21471c14ef7b6608cce6d43a4cd69fff3961c1888c3166a4bebcd",
    "77e7011d94ef2b3a7022013fbef0c2e68f70f1ca9ead58446dd3d4bde78c74c6",
    "f1ce25c5393381750c7582eb7dc783c7625db80a0dad483c1e46cf1b7521b61d",
    "5e8e572496607b58d0ccf93be0bcd1deaaa7d3935f93cef54cccd35e905b3623",
    "b87d02359023eafef439bbf04ce0f9c4929e73bd03a08f3dff955c7f1306d3ac",
]


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "discovery.sqlite"
    initialize_v3_database(path)
    return path


def observe(service, *, mac=MAC_A, source="arp", proposed="unknown-01", ip="192.168.50.71",
            at=NOW, observed=None, dedup="sample-1", metadata=None):
    return service.observe(
        source=source, mac_address=mac, ip_address=ip, proposed_device_id=proposed,
        observed_at=observed or at, received_at=at, deduplication_key=dedup,
        sanitized_metadata=metadata,
    )


def mqtt_payload(device_id="unknown-01", *, mac=MAC_A, ip="192.168.50.71", sequence=0, boot=BOOT):
    return json.dumps({
        "schema_version": 2, "device_id": device_id, "boot_id": boot,
        "sequence": sequence, "firmware_version": "1.2.0", "uptime_ms": 1000 + sequence,
        "ip": ip, "mac": mac, "telemetry": {"device_type": "sensor", "state": "ready"},
    }, separators=(",", ":")).encode("utf-8")


def _manager(path):
    return DeviceManagementService(path, clock=lambda: NOW)


def _create_trusted(manager, device_id="router-01", mac=MAC_B):
    return manager.create_device(
        device_id=device_id, mac=mac, display_name="已有网关", device_type="gateway",
        area_id="home", importance="high", profile_source="gateway", actor=ADMIN,
        request_id="create-trusted-device",
    )


def test_v9_migration_upgrades_and_is_idempotent_without_touching_legacy_assets(tmp_path):
    path = tmp_path / "upgrade-v9.sqlite"
    connection = connect_v3(path)
    try:
        apply_v3_migrations(connection, V3_MIGRATIONS[:8])
        connection.execute("CREATE TABLE assets(id INTEGER PRIMARY KEY, mac TEXT)")
        connection.execute("INSERT INTO assets VALUES(1, ?)", (MAC_A,))
        connection.commit()
        first = apply_v3_migrations(connection)
        repeated = apply_v3_migrations(connection)
        assets = connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        candidates = connection.execute("SELECT COUNT(*) FROM v3_discovered_device_candidates").fetchone()[0]
        table_columns = {row[1] for row in connection.execute("PRAGMA table_info(v3_discovery_observations)")}
    finally:
        connection.close()
    assert [migration.checksum for migration in V3_MIGRATIONS[:8]] == FROZEN_V1_TO_V8
    assert V3_MIGRATIONS[8].name == "unknown_device_discovery"
    assert first["applied_versions"] == [9]
    assert repeated["applied_versions"] == []
    assert repeated["skipped_versions"] == list(range(1, 10))
    assert assets == 1 and candidates == 0
    assert {"evidence_hash", "sanitized_metadata_json", "deduplication_key"} <= table_columns
    plan = plan_database(path)
    assert plan["pending_migrations"] == []


def test_valid_unknown_mqtt_is_quarantined_and_never_writes_trusted_state(database_path):
    state = DeviceStateService(database_path, clock=lambda: NOW, create_if_missing=False)
    discovery = DeviceDiscoveryService(database_path, clock=lambda: NOW)
    result = MqttHeartbeatIngestor(state, discovery_service=discovery).ingest(
        topic="community/unknown-01/status", payload=mqtt_payload(), received_at=NOW,
    )
    assert result.accepted is False
    assert result.code == "unknown_device_discovered"
    assert result.candidate_id
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM v3_device_profiles").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM v3_device_state_observations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM v3_device_current_state").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM v3_mobile_user_scopes").fetchone()[0] == 0
        row = connection.execute("SELECT event_type,payload_json FROM v3_realtime_events").fetchone()
    assert row[0] == "device.discovered"
    assert MAC_A not in row[1] and "192.168.50.71" not in row[1]
    assert "telemetry" not in row[1] and "boot_id" not in row[1]


@pytest.mark.parametrize("topic,payload", [
    ("community/other-01/status", mqtt_payload()),
    ("community/unknown-01/status", b"not-json"),
    ("community/unknown-01/status", mqtt_payload(mac="not-a-mac")),
])
def test_invalid_mqtt_never_enters_discovery(database_path, topic, payload):
    service = DeviceStateService(database_path, clock=lambda: NOW, create_if_missing=False)
    discovery = DeviceDiscoveryService(database_path, clock=lambda: NOW)
    result = MqttHeartbeatIngestor(service, discovery_service=discovery).ingest(
        topic=topic, payload=payload, received_at=NOW,
    )
    assert result.accepted is False
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM v3_discovered_device_candidates").fetchone()[0] == 0


def test_ip_only_unknown_traffic_cannot_create_candidate(database_path):
    service = DeviceDiscoveryService(database_path, clock=lambda: NOW)
    with pytest.raises(DeviceDiscoveryError, match="MAC identity"):
        service.observe(source="arp", mac_address="", ip_address="192.168.50.71",
            proposed_device_id=None, observed_at=NOW, received_at=NOW, deduplication_key="ip-only")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM v3_discovered_device_candidates").fetchone()[0] == 0


def test_same_mac_deduplicates_retries_and_merges_sources(database_path):
    service = DeviceDiscoveryService(database_path, clock=lambda: NOW)
    first = observe(service, source="arp", dedup="arp-1", mac="aabbccddee71")
    duplicate = observe(service, source="arp", dedup="arp-1", mac=MAC_A)
    second_source = observe(service, source="dhcp", dedup="lease-1", mac=MAC_A,
        at=NOW + timedelta(seconds=2))
    assert first["created"] and duplicate["code"] == "duplicate"
    assert second_source["candidate_id"] == first["candidate_id"]
    candidate = service.get_candidate(first["candidate_id"])
    assert candidate["mac_address"] == MAC_A
    assert candidate["source_count"] == 2 and candidate["observation_count"] == 2
    assert candidate["sources"] == ["arp", "dhcp"]


def test_reused_boot_sequence_with_changed_evidence_is_quarantined_not_overwritten(database_path):
    service = DeviceDiscoveryService(database_path, clock=lambda: NOW)
    first = observe(service, source="mqtt_unknown", proposed="unknown-01", ip="192.168.50.71",
        dedup=f"{BOOT}:0", at=NOW)
    altered = observe(service, source="mqtt_unknown", proposed="unknown-01", ip="192.168.50.72",
        dedup=f"{BOOT}:0", at=NOW + timedelta(seconds=1))
    repeated = observe(service, source="mqtt_unknown", proposed="unknown-01", ip="192.168.50.72",
        dedup=f"{BOOT}:0", at=NOW + timedelta(seconds=2), observed=NOW + timedelta(seconds=1))
    candidate = service.get_candidate(first["candidate_id"])
    assert altered["status"] == "conflict"
    assert candidate["status"] == "conflict"
    assert candidate["conflict_reason"] == "deduplication_key_reused"
    assert candidate["observation_count"] == 2
    assert repeated["code"] == "duplicate_conflict_evidence"
    assert candidate["latest_ip"] == "192.168.50.72"


def test_multiple_proposed_ids_and_previously_bound_mac_become_conflicts(database_path):
    service = DeviceDiscoveryService(database_path, clock=lambda: NOW)
    first = observe(service, proposed="unknown-01", dedup="one")
    observe(service, proposed="unknown-02", dedup="two", at=NOW + timedelta(seconds=2))
    conflict = service.get_candidate(first["candidate_id"])
    assert conflict["status"] == "conflict"
    assert conflict["conflict_reason"] == "multiple_proposed_device_ids"
    assert conflict["conflicting_proposed_device_ids"] == ["unknown-01", "unknown-02"]
    with sqlite3.connect(database_path) as connection:
        events = [row[0] for row in connection.execute(
            "SELECT payload_json FROM v3_realtime_events WHERE event_type='device.discovered' ORDER BY event_id"
        )]
    assert len(events) == 2
    assert MAC_A not in "".join(events) and "192.168.50.71" not in "".join(events)

    _create_trusted(_manager(database_path), device_id="trusted-01", mac=MAC_B)
    bound = observe(service, mac=MAC_B, proposed="strange-01", ip="192.168.50.72", dedup="bound")
    assert service.get_candidate(bound["candidate_id"])["conflict_reason"] == "mac_already_bound"


def test_conflicting_proposals_require_explicit_manual_resolution_before_claim(database_path):
    service = DeviceDiscoveryService(database_path, clock=lambda: NOW)
    candidate = observe(service, proposed="untrusted-a", dedup="proposal-a")
    observe(service, proposed="untrusted-b", dedup="proposal-b", at=NOW + timedelta(seconds=1))
    with pytest.raises(CandidateIdentityConflictError):
        service.claim_candidate(candidate["candidate_id"], expected_candidate_version=2,
            device_id="manually-verified", display_name="人工核验设备", device_type="sensor",
            area_id=None, importance="normal", profile_source="physical", actor=ADMIN,
            request_id="claim-conflict-unresolved")

    claimed = service.claim_candidate(candidate["candidate_id"], expected_candidate_version=2,
        device_id="manually-verified", display_name="人工核验设备", device_type="sensor",
        area_id=None, importance="normal", profile_source="physical", actor=ADMIN,
        request_id="claim-conflict-resolved", resolve_identity_conflict=True)
    assert claimed["candidate"]["status"] == "claimed"
    assert claimed["candidate"]["conflict"] is False
    assert claimed["device"]["device_id"] == "manually-verified"
    with sqlite3.connect(database_path) as connection:
        action = connection.execute(
            "SELECT before_json,after_json FROM v3_discovery_actions WHERE request_id=?",
            ("claim-conflict-resolved",),
        ).fetchone()
    assert "multiple_proposed_device_ids" in action[0]
    assert "multiple_proposed_device_ids" not in action[1]


def test_claimed_identity_stays_terminal_but_records_later_mac_conflict(database_path):
    service = DeviceDiscoveryService(database_path, clock=lambda: NOW)
    candidate = observe(service, proposed="observed-01", dedup="before-claim")
    service.claim_candidate(candidate["candidate_id"], expected_candidate_version=1,
        device_id="trusted-01", display_name="人工确认设备", device_type="sensor", area_id=None,
        importance="normal", profile_source="physical", actor=ADMIN, request_id="claim-terminal")
    conflict = observe(service, proposed="unrecognized-99", dedup="after-claim",
        at=NOW + timedelta(seconds=1))
    current = service.get_candidate(candidate["candidate_id"])
    assert conflict["candidate_id"] == candidate["candidate_id"]
    assert current["status"] == "claimed"
    assert current["conflict"] is True and current["conflict_reason"] == "mac_already_bound"
    assert current["claimed_device_id"] == "trusted-01"


def test_candidate_observation_capacity_frequency_and_degraded_health(database_path):
    limited = DeviceDiscoveryService(database_path, clock=lambda: NOW,
        max_candidates=1, max_observations_per_candidate=10,
        max_source_observations_per_minute=1)
    first = observe(limited, dedup="first")
    throttled = observe(limited, dedup="second", at=NOW + timedelta(seconds=1))
    full = observe(limited, mac=MAC_B, proposed="other-01", ip="192.168.50.72", dedup="third")
    assert first["created"]
    assert throttled["code"] == "observation_rate_limited"
    assert full["code"] == "candidate_capacity_reached"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM v3_discovered_device_candidates").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM v3_discovery_observations").fetchone()[0] == 1
        component = connection.execute("SELECT readiness,reason FROM v3_system_component_health WHERE component_id='device_discovery'").fetchone()
    assert component == ("degraded", "candidate_capacity_reached")


def test_ignore_observations_do_not_restore_automatically_and_restore_is_versioned(database_path):
    service = DeviceDiscoveryService(database_path, clock=lambda: NOW)
    candidate = observe(service, dedup="first")
    with pytest.raises(DeviceDiscoveryError, match="secrets or raw payload"):
        service.ignore_candidate(candidate["candidate_id"], expected_candidate_version=1,
            reason="MQTT password=do-not-store", actor=ADMIN, request_id="ignore-secret")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM v3_discovery_actions").fetchone()[0] == 0
    ignored = service.ignore_candidate(candidate["candidate_id"], expected_candidate_version=1,
        reason="人工确认不是社区设备", actor=ADMIN, request_id="ignore-1")
    assert ignored["status"] == "ignored" and ignored["candidate_version"] == 2
    with pytest.raises(CandidateVersionConflictError):
        service.restore_candidate(candidate["candidate_id"], expected_candidate_version=1,
            actor=ADMIN, request_id="restore-stale")
    observe(service, dedup="later", at=NOW + timedelta(seconds=2))
    still_ignored = service.get_candidate(candidate["candidate_id"])
    assert still_ignored["status"] == "ignored" and still_ignored["candidate_version"] == 2
    restored = service.restore_candidate(candidate["candidate_id"], expected_candidate_version=2,
        actor=ADMIN, request_id="restore-1")
    assert restored["status"] == "pending" and restored["candidate_version"] == 3


def test_claim_is_atomic_unknown_and_retains_evidence_without_ip_or_scope(database_path):
    service = DeviceDiscoveryService(database_path, clock=lambda: NOW)
    candidate = observe(service, dedup="claim-me")
    result = service.claim_candidate(candidate["candidate_id"], expected_candidate_version=1,
        device_id="manual-camera", display_name="管理员核验的摄像头", device_type="camera",
        area_id="east", importance="normal", profile_source="physical", actor=ADMIN,
        request_id="claim-1")
    device = result["device"]
    assert result["credential_provisioning_required"] is True
    assert device["connection_status"] == "unknown" and device["ip_address"] is None
    assert device["observed_at"] is None and device["received_at"] is None
    assert device["can_delete"] is False
    assert device["references"]["discovery_candidates"] == 1
    assert result["candidate"]["status"] == "claimed"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM v3_device_state_observations WHERE device_id='manual-camera'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM v3_mobile_user_scopes").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM v3_discovery_actions WHERE action='claimed'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM v3_device_management_audit WHERE device_id='manual-camera' AND action='created'").fetchone()[0] == 1
        event_types = [r[0] for r in connection.execute("SELECT event_type FROM v3_realtime_events ORDER BY event_id")]
    assert "device.discovered" in event_types and "device.inventory_changed" in event_types
    with pytest.raises(DeviceDiscoveryError):
        service.claim_candidate(candidate["candidate_id"], expected_candidate_version=2,
            device_id="second-id", display_name="再次认领", device_type="sensor", area_id=None,
            importance="normal", profile_source="physical", actor=ADMIN, request_id="claim-again")


def test_bound_identity_cannot_be_claimed_and_failed_claim_rolls_back(database_path):
    manager = _manager(database_path)
    _create_trusted(manager, device_id="taken-id", mac=MAC_B)
    service = DeviceDiscoveryService(database_path, clock=lambda: NOW)
    bound = observe(service, mac=MAC_B, proposed="other-01", ip="192.168.50.72", dedup="bound")
    with pytest.raises(CandidateIdentityConflictError):
        service.claim_candidate(bound["candidate_id"], expected_candidate_version=1,
            device_id="new-id", display_name="新设备", device_type="sensor", area_id=None,
            importance="normal", profile_source="physical", actor=ADMIN, request_id="blocked-claim")

    pending = observe(service, mac=MAC_A, proposed="candidate-01", dedup="pending")
    with pytest.raises(DeviceIdConflictError):
        service.claim_candidate(pending["candidate_id"], expected_candidate_version=1,
            device_id="taken-id", display_name="重复 ID", device_type="sensor", area_id=None,
            importance="normal", profile_source="physical", actor=ADMIN, request_id="failed-claim")
    assert service.get_candidate(pending["candidate_id"])["status"] == "pending"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM v3_device_management_audit WHERE request_id='failed-claim'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM v3_discovery_actions WHERE candidate_id=?", (pending["candidate_id"],)).fetchone()[0] == 0


def _api(path: Path):
    app = Flask(__name__)
    app.secret_key = "discovery-api-test-key"
    app.register_blueprint(create_v3_devices_blueprint(path, clock=lambda: NOW))
    app.register_blueprint(create_v3_device_discovery_blueprint(path, clock=lambda: NOW))
    return app.test_client()


def _login(client, role="admin"):
    with client.session_transaction() as state:
        state["user_id"] = {"admin": 1, "operator": 2, "user": 3}[role]
        state["username"] = f"test-{role}"
        state["role"] = role
    return client.get("/api/v3/devices").headers["X-CSRF-Token"]


def test_api_permissions_csrf_redaction_pagination_and_claim(database_path):
    service = DeviceDiscoveryService(database_path, clock=lambda: NOW)
    one = observe(service, proposed="candidate-01", dedup="one")
    observe(service, mac=MAC_B, proposed="candidate-02", ip="192.168.50.72", dedup="two", at=NOW + timedelta(seconds=1))
    client = _api(database_path)
    assert client.get("/api/v3/devices/discovered").status_code == 401
    assert client.get("/api/v3/devices/discovered", headers={"Authorization": "Bearer mobile-token"}).status_code == 401

    _login(client, "operator")
    operator_list = client.get("/api/v3/devices/discovered?limit=1&offset=0").get_json()
    assert operator_list["total"] == 2 and len(operator_list["items"]) == 1
    assert "mac_address" not in operator_list["items"][0] and "latest_ip" not in operator_list["items"][0]
    operator_detail = client.get(f"/api/v3/devices/discovered/{one['candidate_id']}").get_json()["candidate"]
    assert "mac_address" not in operator_detail
    assert all("ip_address" not in item and "evidence_hash" not in item for item in operator_detail["observations"])
    assert client.post(f"/api/v3/devices/discovered/{one['candidate_id']}/ignore", json={"expected_candidate_version": 1, "reason": "x"}).status_code == 403

    _login(client, "user")
    assert client.get("/api/v3/devices/discovered").status_code == 403

    csrf = _login(client, "admin")
    admin_detail = client.get(f"/api/v3/devices/discovered/{one['candidate_id']}").get_json()["candidate"]
    assert admin_detail["mac_address"] == MAC_A and admin_detail["latest_ip"] == "192.168.50.71"
    assert client.post(f"/api/v3/devices/discovered/{one['candidate_id']}/ignore", json={
        "expected_candidate_version": 1, "reason": "人工确认", "extra": "rejected",
    }, headers={"X-CSRF-Token": csrf}).status_code == 400
    claim = client.post(f"/api/v3/devices/discovered/{one['candidate_id']}/claim", json={
        "expected_candidate_version": 1, "device_id": "verified-camera", "display_name": "人工核验设备",
        "device_type": "camera", "area_id": "home", "importance": "normal", "profile_source": "physical",
    }, headers={"X-CSRF-Token": csrf})
    assert claim.status_code == 201
    assert claim.get_json()["device"]["connection_status"] == "unknown"
    assert claim.get_json()["credential_provisioning_required"] is True


def test_missing_database_returns_503_without_creating_file(tmp_path):
    missing = tmp_path / "not-created.sqlite"
    client = _api(missing)
    _login(client)
    response = client.get("/api/v3/devices/discovered")
    assert response.status_code == 503
    assert response.get_json()["error"]["request_id"]
    assert missing.exists() is False
