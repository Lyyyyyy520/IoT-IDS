from datetime import datetime, timedelta, timezone
import json
import sqlite3

from services.device_state import DeviceStateService
from services.mqtt_ingestion import MqttHeartbeatIngestor
from services.realtime_events import RealtimeEventStore
from v3_database import (
    V3_DEVICE_LIFECYCLE_MIGRATION,
    V3_MIGRATIONS,
    V3_REALTIME_EVENT_MIGRATION,
    apply_v3_migrations,
    connect_v3,
    initialize_v3_database,
)


MIGRATION_V1_CHECKSUM = (
    "3fe72003fd5eb35063bd5aea3f677bc66ef0aaaa36cded58a83f46738bd26952"
)
MIGRATION_V2_CHECKSUM = (
    "77ce4e371366c7d3e53640debb212c9a736fd5e9f8849b06e6d3700508d48078"
)
MIGRATION_V3_CHECKSUM = (
    "bfe9842f09d391284b408dd0df36e205e4ad9f92361ee304dd52955c9f6aa329"
)
MIGRATION_V4_CHECKSUM = (
    "685caf41c5d21471c14ef7b6608cce6d43a4cd69fff3961c1888c3166a4bebcd"
)
START = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)
BOOT_ID = "a" * 32
DEVICE_MAC = "AA:BB:CC:DD:EE:02"


class FakeClock:
    def __init__(self, current: datetime):
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **delta) -> None:
        self.current += timedelta(**delta)


def _events(path):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return [
            {**dict(row), "payload": json.loads(row["payload_json"])}
            for row in connection.execute(
                "SELECT event_id, event_type, occurred_at, device_id, "
                "state_version, payload_json FROM v3_realtime_events "
                "ORDER BY event_id"
            )
        ]


def _bind(service: DeviceStateService) -> None:
    service.bind_device(
        device_id="camera-01",
        identity_kind="mac",
        identity_value=DEVICE_MAC,
        display_name="客厅摄像头",
        device_type="camera",
        area_id="building-a",
    )


def _heartbeat(sequence: int, uptime_ms: int) -> bytes:
    return json.dumps(
        {
            "schema_version": 2,
            "device_id": "camera-01",
            "boot_id": BOOT_ID,
            "sequence": sequence,
            "firmware_version": "0.3.0",
            "uptime_ms": uptime_ms,
            "ip": "192.168.4.21",
            "mac": DEVICE_MAC,
            "telemetry": {"device_type": "camera", "state": "recording"},
        },
        separators=(",", ":"),
    ).encode("utf-8")


def test_migration_v1_v2_v3_checksums_are_frozen_and_v4_is_repeatable(tmp_path):
    database_path = tmp_path / "upgrade-v2.sqlite"
    assert V3_MIGRATIONS[0].checksum == MIGRATION_V1_CHECKSUM
    assert V3_MIGRATIONS[1].checksum == MIGRATION_V2_CHECKSUM
    assert V3_REALTIME_EVENT_MIGRATION.checksum == MIGRATION_V3_CHECKSUM
    assert V3_DEVICE_LIFECYCLE_MIGRATION.checksum == MIGRATION_V4_CHECKSUM

    connection = connect_v3(database_path)
    try:
        first = apply_v3_migrations(connection, V3_MIGRATIONS[:2])
        upgraded = apply_v3_migrations(connection)
        repeated = apply_v3_migrations(connection)
        versions = connection.execute(
            "SELECT version FROM v3_schema_migrations ORDER BY version"
        ).fetchall()
    finally:
        connection.close()

    assert first["applied_versions"] == [1, 2]
    assert upgraded["applied_versions"] == [3, 4, 5, 6, 7, 8, 9]
    assert upgraded["skipped_versions"] == [1, 2]
    assert repeated["applied_versions"] == []
    assert repeated["skipped_versions"] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert [row[0] for row in versions] == [1, 2, 3, 4, 5, 6, 7, 8, 9]


def test_accepted_mqtt_writes_events_and_rejected_replay_writes_none(tmp_path):
    clock = FakeClock(START)
    service = DeviceStateService(tmp_path / "mqtt-events.sqlite", clock=clock)
    service.initialize()
    _bind(service)
    ingestor = MqttHeartbeatIngestor(service)

    first = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_heartbeat(1, 1000),
        received_at=clock.current,
    )
    duplicate = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_heartbeat(1, 1000),
        received_at=clock.current + timedelta(seconds=1),
    )
    second = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_heartbeat(2, 2000),
        received_at=clock.current + timedelta(seconds=2),
    )

    events = _events(service.database_path)
    assert first.accepted and second.accepted
    assert duplicate.code == "duplicate_sequence"
    assert [event["event_type"] for event in events] == [
        "device.connection_changed",
        "device.telemetry_updated",
    ]
    assert [event["state_version"] for event in events] == [1, 2]
    assert events[0]["payload"]["from"] == "unknown"
    assert events[0]["payload"]["to"] == "online"
    assert events[0]["payload"]["connection_status"] == "online"
    assert events[0]["payload"]["ip_address"] == "192.168.4.21"
    assert events[0]["payload"]["observed_at"] == "2026-09-19T08:00:00Z"
    assert events[0]["payload"]["received_at"] == "2026-09-19T08:00:00Z"
    assert events[0]["payload"]["sources"] == [f"mqtt:{BOOT_ID}"]
    assert events[1]["payload"]["connection_status"] == "online"
    assert events[1]["payload"]["received_at"] == "2026-09-19T08:00:02Z"


def test_timeout_events_are_ordered_and_noop_refresh_is_silent(tmp_path):
    clock = FakeClock(START)
    service = DeviceStateService(tmp_path / "timeouts.sqlite", clock=clock)
    service.initialize()
    _bind(service)
    service.record_observation(
        device_id="camera-01",
        identity_kind="mac",
        identity_value=DEVICE_MAC,
        source="probe-a",
        ip_address="192.168.4.21",
        sequence=1,
    )

    assert service.refresh_connection_statuses() == 0
    clock.advance(seconds=15)
    assert service.refresh_connection_statuses() == 1
    assert service.refresh_connection_statuses() == 0
    clock.advance(seconds=15)
    assert service.refresh_connection_statuses() == 1

    events = _events(service.database_path)
    assert [event["payload"].get("to") for event in events] == [
        "online",
        "stale",
        "offline",
    ]
    assert [event["state_version"] for event in events] == [1, 2, 3]
    assert [event["event_id"] for event in events] == [1, 2, 3]
    assert events[-1]["payload"] == {
        "connection_status": "offline",
        "from": "stale",
        "ip_address": "192.168.4.21",
        "observed_at": "2026-09-19T08:00:00Z",
        "received_at": "2026-09-19T08:00:00Z",
        "source": "timeout",
        "sources": ["probe-a"],
        "to": "offline",
    }


def test_component_event_requires_actual_readiness_or_reason_change(tmp_path):
    clock = FakeClock(START)
    service = DeviceStateService(tmp_path / "components.sqlite", clock=clock)
    service.initialize()

    first = service.set_component_readiness("mqtt-subscriber", "ready")
    unchanged = service.set_component_readiness("mqtt-subscriber", "ready")
    clock.advance(seconds=1)
    degraded = service.set_component_readiness(
        "mqtt-subscriber", "degraded", "broker disconnected"
    )

    events = _events(service.database_path)
    assert first["state_version"] == unchanged["state_version"] == 1
    assert degraded["state_version"] == 2
    assert [event["event_type"] for event in events] == [
        "system.component_changed",
        "system.component_changed",
    ]
    assert events[-1]["payload"]["reason"] == "broker disconnected"
    assert events[-1]["payload"]["readiness"] == "degraded"
    assert events[-1]["payload"]["started_at"] == "2026-09-19T08:00:00Z"
    assert events[-1]["payload"]["ready_at"] == "2026-09-19T08:00:00Z"
    assert events[-1]["payload"]["updated_at"] == "2026-09-19T08:00:01Z"


def test_event_store_replays_after_restart_and_detects_a_gap(tmp_path):
    database_path = tmp_path / "replay.sqlite"
    initialize_v3_database(database_path)
    first_store = RealtimeEventStore(database_path)
    for index in range(3):
        first_store.append(
            event_type="system.component_changed",
            occurred_at=START + timedelta(seconds=index),
            payload={"component_id": f"component-{index}"},
        )

    restarted_store = RealtimeEventStore(database_path)
    replay = restarted_store.read_after(1, limit=8)
    assert [event["event_id"] for event in replay.events] == [2, 3]
    assert replay.current_cursor == 3
    assert replay.requires_snapshot is False

    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM v3_realtime_events WHERE event_id = 2")
    gap = restarted_store.read_after(1, limit=8)
    assert gap.requires_snapshot is True
    assert gap.reason == "event_log_gap"
