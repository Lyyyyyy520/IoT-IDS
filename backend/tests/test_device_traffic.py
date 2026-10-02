from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from services.device_management import DeviceActor, DeviceHasHistoryError, DeviceManagementService
from services.device_state import DeviceStateService
from services.device_traffic import (
    DeviceTrafficService,
    RealtimeTrafficWindow,
    TrafficQueryError,
    TrafficValidationError,
    resolve_ip_binding,
)
from v3_database import (
    V3_DEVICE_TRAFFIC_MIGRATION,
    V3_MIGRATIONS,
    apply_v3_migrations,
    connect_v3,
    connect_v3_existing,
    initialize_v3_database,
)


NOW = datetime(2026, 9, 21, 8, 30, tzinfo=timezone.utc)
FROZEN_CHECKSUMS = [
    "3fe72003fd5eb35063bd5aea3f677bc66ef0aaaa36cded58a83f46738bd26952",
    "77ce4e371366c7d3e53640debb212c9a736fd5e9f8849b06e6d3700508d48078",
    "bfe9842f09d391284b408dd0df36e205e4ad9f92361ee304dd52955c9f6aa329",
    "685caf41c5d21471c14ef7b6608cce6d43a4cd69fff3961c1888c3166a4bebcd",
]
ACTOR = DeviceActor(1, "traffic-admin", "admin")


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "traffic.sqlite"
    initialize_v3_database(path)
    return path


def create_device(path: Path, device_id: str, mac: str) -> None:
    DeviceManagementService(path, clock=lambda: NOW - timedelta(hours=2)).create_device(
        device_id=device_id,
        mac=mac,
        display_name=device_id,
        device_type="sensor",
        profile_source="physical",
        actor=ACTOR,
        request_id=f"create-{device_id}",
    )


def bind(path: Path, device_id: str, mac: str, ip: str, at: datetime) -> None:
    DeviceStateService(path, clock=lambda: at).record_observation(
        device_id=device_id,
        identity_kind="mac",
        identity_value=mac,
        source="test-observation",
        ip_address=ip,
        observed_at=at - timedelta(seconds=1),
        received_at=at,
    )


def sample(sample_id: str, *, at: datetime, src: str, dst: str,
           protocol: str = "TCP", byte_count: int = 120, packets: int = 1,
           flows: int = 1) -> dict:
    return {
        "sample_id": sample_id,
        "occurred_at": at.isoformat(),
        "src_ip": src,
        "dst_ip": dst,
        "network_protocol": protocol,
        "application_protocol": None,
        "application_protocol_inferred": False,
        "src_port": 12345,
        "dst_port": 443,
        "bytes": byte_count,
        "packets": packets,
        "flow_count": flows,
    }


def ingest(service: DeviceTrafficService, batch_id: str, sequence: int, rows: list[dict]):
    return service.ingest_batch(
        source_id="test-probe",
        source_session_id="boot-a",
        batch_id=batch_id,
        batch_sequence=sequence,
        samples=rows,
        received_at=NOW,
    )


def test_v5_migration_new_upgrade_and_idempotent(tmp_path: Path):
    path = tmp_path / "upgrade.sqlite"
    connection = connect_v3(path)
    try:
        first = apply_v3_migrations(connection, V3_MIGRATIONS[:4])
        connection.execute("CREATE TABLE assets (id INTEGER PRIMARY KEY, ip_address TEXT)")
        connection.execute("INSERT INTO assets VALUES (1, '192.0.2.1')")
        connection.commit()
        upgrade = apply_v3_migrations(connection)
        repeated = apply_v3_migrations(connection)
        legacy_count = connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        bindings = connection.execute("SELECT COUNT(*) FROM v3_device_ip_bindings").fetchone()[0]
    finally:
        connection.close()
    assert [item.checksum for item in V3_MIGRATIONS[:4]] == FROZEN_CHECKSUMS
    assert V3_DEVICE_TRAFFIC_MIGRATION.checksum == (
        "77e7011d94ef2b3a7022013fbef0c2e68f70f1ca9ead58446dd3d4bde78c74c6"
    )
    assert first["applied_versions"] == [1, 2, 3, 4]
    assert upgrade["applied_versions"] == [5, 6, 7, 8, 9]
    assert repeated["applied_versions"] == []
    assert repeated["skipped_versions"] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert legacy_count == 1
    assert bindings == 0


def test_ip_binding_changes_and_resolves_historical_time(database_path: Path):
    create_device(database_path, "camera-01", "AA:BB:CC:DD:EE:01")
    first = NOW - timedelta(hours=1)
    changed = NOW - timedelta(minutes=20)
    bind(database_path, "camera-01", "AA:BB:CC:DD:EE:01", "192.168.1.10", first)
    bind(database_path, "camera-01", "AA:BB:CC:DD:EE:01", "192.168.1.11", changed)
    connection = connect_v3_existing(database_path)
    try:
        old = resolve_ip_binding(connection, "192.168.1.10", first + timedelta(minutes=1))
        expired = resolve_ip_binding(connection, "192.168.1.10", changed + timedelta(seconds=1))
        current = resolve_ip_binding(connection, "192.168.1.11", changed)
        rows = connection.execute(
            "SELECT ip_address, valid_from, valid_to FROM v3_device_ip_bindings ORDER BY binding_id"
        ).fetchall()
    finally:
        connection.close()
    assert old == ("resolved", "camera-01")
    assert expired == ("unknown_ip", None)
    assert current == ("resolved", "camera-01")
    assert rows[0]["valid_to"] == changed.isoformat().replace("+00:00", "Z")
    assert rows[1]["valid_to"] is None


def test_ip_conflict_and_unknown_ip_are_not_guessed(database_path: Path):
    for suffix in (1, 2):
        create_device(
            database_path, f"sensor-{suffix:02d}", f"AA:BB:CC:DD:EE:{suffix:02d}"
        )
        bind(
            database_path, f"sensor-{suffix:02d}",
            f"AA:BB:CC:DD:EE:{suffix:02d}", "192.168.1.55",
            NOW - timedelta(hours=1),
        )
    service = DeviceTrafficService(database_path, clock=lambda: NOW)
    result = ingest(service, "conflict", 1, [sample(
        "c1", at=NOW - timedelta(minutes=1),
        src="192.168.1.55", dst="203.0.113.10",
    )])
    connection = connect_v3_existing(database_path)
    try:
        aggregate_count = connection.execute(
            "SELECT COUNT(*) FROM v3_device_traffic_minutes"
        ).fetchone()[0]
        profile_count = connection.execute(
            "SELECT COUNT(*) FROM v3_device_profiles"
        ).fetchone()[0]
    finally:
        connection.close()
    assert result["unassigned_samples"] == 1
    assert aggregate_count == 0
    assert profile_count == 2


def test_tx_rx_managed_peers_protocol_and_utc_minute(database_path: Path):
    create_device(database_path, "camera-01", "AA:BB:CC:DD:EE:01")
    create_device(database_path, "gateway-01", "AA:BB:CC:DD:EE:02")
    binding_time = NOW - timedelta(hours=1)
    bind(database_path, "camera-01", "AA:BB:CC:DD:EE:01", "192.168.1.10", binding_time)
    bind(database_path, "gateway-01", "AA:BB:CC:DD:EE:02", "192.168.1.1", binding_time)
    at = datetime(2026, 9, 21, 16, 29, 45, tzinfo=timezone(timedelta(hours=8)))
    service = DeviceTrafficService(database_path, clock=lambda: NOW)
    result = ingest(service, "managed", 2, [sample(
        "m1", at=at, src="192.168.1.10", dst="192.168.1.1",
        protocol="UDP", byte_count=500, packets=2, flows=1,
    )])
    camera = service.query_traffic(
        "camera-01", start=NOW - timedelta(hours=1), end=NOW,
        resolution="minute",
    )
    gateway_peers = service.query_peers(
        "gateway-01", start=NOW - timedelta(hours=1), end=NOW,
        direction="rx", protocol="udp",
    )
    camera_peers = service.query_peers(
        "camera-01", start=NOW - timedelta(hours=1), end=NOW,
        direction="tx", protocol="udp",
    )
    assert result["accepted_samples"] == 1
    assert camera["series"] == [{
        "bucket_start": "2026-09-21T08:29:00Z",
        "tx_bytes": 500, "rx_bytes": 0,
        "tx_packets": 2, "rx_packets": 0,
        "tx_flows": 1, "rx_flows": 0,
    }]
    assert camera["protocols"][0]["protocol"] == "UDP"
    assert camera["protocols"][0]["evidence"] == "network_protocol"
    assert gateway_peers["peers"][0]["peer_device_id"] == "camera-01"
    assert gateway_peers["peers"][0]["direction"] == "rx"
    assert camera_peers["peers"][0]["peer_device_id"] == "gateway-01"
    assert camera_peers["peers"][0]["direction"] == "tx"


def test_external_peer_and_batch_retry_do_not_double_count(database_path: Path):
    create_device(database_path, "camera-01", "AA:BB:CC:DD:EE:01")
    bind(
        database_path, "camera-01", "AA:BB:CC:DD:EE:01", "192.168.1.10",
        NOW - timedelta(hours=1),
    )
    service = DeviceTrafficService(database_path, clock=lambda: NOW)
    rows = [sample(
        "e1", at=NOW - timedelta(minutes=2),
        src="192.168.1.10", dst="203.0.113.20", byte_count=321,
    )]
    first = ingest(service, "retry", 3, rows)
    second = ingest(service, "retry", 3, rows)
    traffic = service.query_traffic(
        "camera-01", start=NOW - timedelta(hours=1), end=NOW,
    )
    peers = service.query_peers(
        "camera-01", start=NOW - timedelta(hours=1), end=NOW,
    )
    assert first["status"] == "committed"
    assert second["status"] == "duplicate"
    assert traffic["summary"]["tx_bytes"] == 321
    assert peers["peers"][0]["peer_device_id"] is None
    assert peers["peers"][0]["peer_ip"] == "203.0.113.20"


def test_partial_bad_samples_and_transaction_failure_roll_back(database_path: Path):
    create_device(database_path, "camera-01", "AA:BB:CC:DD:EE:01")
    bind(
        database_path, "camera-01", "AA:BB:CC:DD:EE:01", "192.168.1.10",
        NOW - timedelta(hours=1),
    )
    service = DeviceTrafficService(database_path, clock=lambda: NOW)
    result = ingest(service, "partial", 4, [
        sample("good", at=NOW - timedelta(minutes=1), src="192.168.1.10", dst="203.0.113.1"),
        {**sample("bad", at=NOW, src="not-an-ip", dst="203.0.113.1")},
    ])
    assert result["status"] == "partial"
    assert result["accepted_samples"] == 1
    assert result["rejected_samples"] == 1
    assert result["errors"] == [{"index": 1, "code": "invalid_ip"}]

    failing = DeviceTrafficService(
        database_path,
        clock=lambda: NOW,
        fault_injector=lambda _connection: (_ for _ in ()).throw(RuntimeError("test")),
    )
    with pytest.raises(RuntimeError):
        ingest(failing, "rollback", 5, [sample(
            "rollback", at=NOW - timedelta(seconds=30),
            src="192.168.1.10", dst="203.0.113.2",
        )])
    connection = connect_v3_existing(database_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_traffic_ingest_batches WHERE batch_id = 'rollback'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_traffic_ingest_samples WHERE sample_id = 'rollback'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_validation_retention_and_realtime_window_bounds(database_path: Path):
    service = DeviceTrafficService(database_path, clock=lambda: NOW, max_batch_samples=1)
    with pytest.raises(TrafficValidationError) as too_large:
        ingest(service, "large", 6, [
            sample("a", at=NOW, src="192.0.2.1", dst="192.0.2.2"),
            sample("b", at=NOW, src="192.0.2.1", dst="192.0.2.2"),
        ])
    assert too_large.value.code == "batch_too_large"
    old = ingest(service, "old", 7, [sample(
        "old", at=NOW - timedelta(days=31), src="192.0.2.1", dst="192.0.2.2"
    )])
    assert old["errors"] == [{"index": 0, "code": "sample_outside_retention"}]

    window = RealtimeTrafficWindow(
        clock=lambda: NOW, retention_seconds=60, max_devices=1, max_buckets_per_device=2
    )
    empty = window.snapshot("camera-01")
    assert empty["available"] is False
    assert empty["readiness"] == "warming_up"
    from services.device_traffic import TrafficSample
    for index in range(3):
        parsed = TrafficSample.from_mapping(
            sample(
                f"w{index}", at=NOW - timedelta(seconds=2 - index),
                src="192.0.2.1", dst="192.0.2.2",
            ),
            source_id="window", received_at=NOW,
        )
        window.add("camera-01", "tx", parsed)
    window.add("camera-02", "rx", parsed)
    assert window.stats() == {"device_count": 1, "bucket_count": 1}


def test_no_data_query_limits_and_no_raw_payload_columns(database_path: Path):
    create_device(database_path, "camera-01", "AA:BB:CC:DD:EE:01")
    service = DeviceTrafficService(database_path, clock=lambda: NOW)
    result = service.query_traffic(
        "camera-01", start=NOW - timedelta(hours=1), end=NOW,
    )
    assert result["availability"] == {
        "available": False, "reason": "no_samples", "latest_sample_at": None,
    }
    assert result["summary"] is None
    assert result["realtime"]["available"] is False
    with pytest.raises(TrafficQueryError) as range_error:
        service.query_traffic(
            "camera-01", start=NOW - timedelta(days=31), end=NOW,
        )
    assert range_error.value.code == "query_range_too_large"
    with pytest.raises(TrafficQueryError) as point_error:
        service.query_traffic(
            "camera-01", start=NOW - timedelta(hours=2), end=NOW,
            resolution="minute", max_points=10,
        )
    assert point_error.value.code == "too_many_data_points"
    connection = connect_v3_existing(database_path)
    try:
        columns = {
            row[1]
            for table in (
                "v3_device_traffic_minutes", "v3_device_traffic_protocol_minutes",
                "v3_device_traffic_peer_minutes", "v3_traffic_ingest_samples",
            )
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
    finally:
        connection.close()
    assert not {"payload", "payload_json", "cookie", "password", "token"} & columns


def test_retention_cleanup_is_explicit_and_keeps_identity_history(database_path: Path):
    create_device(database_path, "camera-01", "AA:BB:CC:DD:EE:01")
    bind(
        database_path, "camera-01", "AA:BB:CC:DD:EE:01", "192.168.1.10",
        NOW - timedelta(days=2),
    )
    service = DeviceTrafficService(database_path, clock=lambda: NOW)
    ingest(service, "purge", 9, [sample(
        "purge", at=NOW - timedelta(days=1),
        src="192.168.1.10", dst="203.0.113.10",
    )])
    removed = service.purge_before(NOW)
    connection = connect_v3_existing(database_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_device_ip_bindings"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_device_traffic_minutes"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_traffic_ingest_batches"
        ).fetchone()[0] == 0
    finally:
        connection.close()
    assert removed["v3_device_traffic_minutes"] == 1
    assert removed["v3_traffic_ingest_samples"] == 1


def test_traffic_history_blocks_hard_delete(database_path: Path):
    create_device(database_path, "camera-01", "AA:BB:CC:DD:EE:01")
    bind(
        database_path, "camera-01", "AA:BB:CC:DD:EE:01", "192.168.1.10",
        NOW - timedelta(hours=1),
    )
    service = DeviceTrafficService(database_path, clock=lambda: NOW)
    ingest(service, "history", 8, [sample(
        "history", at=NOW - timedelta(minutes=1),
        src="192.168.1.10", dst="203.0.113.10",
    )])
    management = DeviceManagementService(database_path, clock=lambda: NOW)
    with pytest.raises(DeviceHasHistoryError) as exc:
        management.delete_device(
            "camera-01", confirmation="camera-01", actor=ACTOR,
            request_id="delete-history",
        )
    assert "traffic_minutes" in exc.value.references["blocking_reasons"]
    assert exc.value.references["counts"]["traffic_minutes"] == 1
