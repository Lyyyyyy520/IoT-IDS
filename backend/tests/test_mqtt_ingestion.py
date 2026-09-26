from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from contracts import MQTT_HEARTBEAT_SCHEMA_VERSION
from services.device_state import DeviceStateService
from services.mqtt_ingestion import (
    MqttHeartbeatIngestor,
    MqttHeartbeatValidator,
)
from v3_database import (
    V3_MIGRATIONS,
    V3_MQTT_HEARTBEAT_TABLES,
    initialize_v3_database,
)


MIGRATION_V1_CHECKSUM = "3fe72003fd5eb35063bd5aea3f677bc66ef0aaaa36cded58a83f46738bd26952"
BOOT_A = "a" * 32
BOOT_B = "b" * 32
DEVICE_MAC = "AA:BB:CC:DD:EE:02"
START = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, current: datetime):
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def set(self, current: datetime) -> None:
        self.current = current


def _payload(
    *,
    device_id="camera-01",
    boot_id=BOOT_A,
    sequence=1,
    uptime_ms=1000,
    ip="192.168.4.21",
    mac=DEVICE_MAC,
    schema_version=MQTT_HEARTBEAT_SCHEMA_VERSION,
    telemetry=None,
    device_time=None,
) -> bytes:
    document = {
        "schema_version": schema_version,
        "device_id": device_id,
        "boot_id": boot_id,
        "sequence": sequence,
        "firmware_version": "0.3.0",
        "uptime_ms": uptime_ms,
        "ip": ip,
        "mac": mac,
        "telemetry": telemetry or {
            "device_type": "camera",
            "state": "recording",
            "angle": 90,
        },
    }
    if device_time is not None:
        document["device_time"] = device_time
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


@pytest.fixture
def mqtt_state(tmp_path):
    clock = FakeClock(START)
    service = DeviceStateService(tmp_path / "mqtt.sqlite", clock=clock)
    service.initialize()
    service.bind_device(
        device_id="camera-01",
        identity_kind="mac",
        identity_value=DEVICE_MAC,
        display_name="客厅摄像头",
        device_type="camera",
        area_id="building-a",
    )
    return service, MqttHeartbeatIngestor(service), clock


def _observation_count(service: DeviceStateService) -> int:
    with sqlite3.connect(service.database_path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM v3_device_state_observations"
        ).fetchone()[0]


def test_valid_heartbeat_uses_backend_received_time_and_updates_state(mqtt_state):
    service, ingestor, _clock = mqtt_state
    result = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(device_time="2001-01-01T00:00:00Z"),
        received_at=START,
    )

    assert result.accepted is True
    assert result.code == "accepted"
    with sqlite3.connect(service.database_path) as connection:
        connection.row_factory = sqlite3.Row
        current = connection.execute(
            "SELECT connection_status, last_observed_at, last_received_at "
            "FROM v3_device_current_state WHERE device_id = 'camera-01'"
        ).fetchone()
        observation = connection.execute(
            "SELECT boot_id, sequence, firmware_version, uptime_ms, payload_json "
            "FROM v3_device_state_observations"
        ).fetchone()
    assert current["connection_status"] == "online"
    assert current["last_observed_at"] == "2001-01-01T00:00:00Z"
    assert current["last_received_at"] == "2026-09-19T08:00:00Z"
    assert observation["boot_id"] == BOOT_A
    assert observation["sequence"] == 1
    assert observation["firmware_version"] == "0.3.0"
    assert observation["uptime_ms"] == 1000
    assert json.loads(observation["payload_json"])["device_type"] == "camera"


def test_topic_and_payload_device_id_must_match(mqtt_state):
    _service, ingestor, _clock = mqtt_state
    result = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(device_id="door-01"),
        received_at=START,
    )
    assert result.accepted is False
    assert result.code == "device_id_mismatch"


@pytest.mark.parametrize(
    "topic",
    [
        "community/camera-01/state",
        "community/camera-01/status/extra",
        "community/Camera-01/status",
        "other/camera-01/status",
    ],
)
def test_topic_format_is_strict(mqtt_state, topic):
    _service, ingestor, _clock = mqtt_state
    result = ingestor.ingest(
        topic=topic,
        payload=_payload(),
        received_at=START,
    )
    assert result.code == "invalid_topic"


def test_registered_mac_identity_must_match(mqtt_state):
    service, ingestor, _clock = mqtt_state
    result = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(mac="AA:BB:CC:DD:EE:04"),
        received_at=START,
    )
    assert result.code == "identity_mismatch"
    assert _observation_count(service) == 0


def test_unknown_device_is_not_auto_registered(mqtt_state):
    service, ingestor, _clock = mqtt_state
    result = ingestor.ingest(
        topic="community/door-99/status",
        payload=_payload(
            device_id="door-99",
            mac="AA:BB:CC:DD:EE:06",
        ),
        received_at=START,
    )
    assert result.code == "unknown_device"
    with sqlite3.connect(service.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v3_device_profiles"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (b"{not-json", "invalid_json"),
        (b"\xff\xfe", "invalid_utf8"),
        (_payload(ip="not-an-ip"), "invalid_ip"),
        (_payload(mac="not-a-mac"), "invalid_mac"),
        (_payload(mac="AA::BB:CC:DD:EE:02"), "invalid_mac"),
        (b"x" * 4097, "payload_too_large"),
    ],
)
def test_malformed_payloads_are_rejected(mqtt_state, payload, expected_code):
    _service, ingestor, _clock = mqtt_state
    result = ingestor.ingest(
        topic="community/camera-01/status",
        payload=payload,
        received_at=START,
    )
    assert result.accepted is False
    assert result.code == expected_code


def test_telemetry_has_an_independent_size_limit(mqtt_state):
    service, _ingestor, _clock = mqtt_state
    validator = MqttHeartbeatValidator(
        max_payload_bytes=4096,
        max_telemetry_bytes=32,
    )
    ingestor = MqttHeartbeatIngestor(service, validator)
    result = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(telemetry={"sample": "x" * 64}),
        received_at=START,
    )
    assert result.code == "telemetry_too_large"


def test_unsupported_schema_version_is_rejected(mqtt_state):
    _service, ingestor, _clock = mqtt_state
    result = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(schema_version=1),
        received_at=START,
    )
    assert result.code == "unsupported_schema_version"


def test_same_boot_duplicate_and_out_of_order_sequences_are_rejected(mqtt_state):
    service, ingestor, _clock = mqtt_state
    accepted_one = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(sequence=1, uptime_ms=1000),
        received_at=START,
    )
    accepted_three = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(sequence=3, uptime_ms=3000),
        received_at=START + timedelta(seconds=5),
    )
    duplicate = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(sequence=3, uptime_ms=3000),
        received_at=START + timedelta(seconds=6),
    )
    out_of_order = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(sequence=2, uptime_ms=2000),
        received_at=START + timedelta(seconds=7),
    )

    assert accepted_one.accepted and accepted_three.accepted
    assert duplicate.code == "duplicate_sequence"
    assert out_of_order.code == "out_of_order_sequence"
    assert _observation_count(service) == 2


def test_new_boot_can_reset_sequence_but_old_boot_replay_is_rejected(mqtt_state):
    service, ingestor, _clock = mqtt_state
    first = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(boot_id=BOOT_A, sequence=1, uptime_ms=1000),
        received_at=START,
    )
    restarted = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(boot_id=BOOT_B, sequence=1, uptime_ms=100),
        received_at=START + timedelta(seconds=5),
    )
    replay = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(boot_id=BOOT_A, sequence=2, uptime_ms=2000),
        received_at=START + timedelta(seconds=10),
    )

    assert first.accepted and restarted.accepted
    assert replay.code == "replayed_boot"
    assert _observation_count(service) == 2
    with sqlite3.connect(service.database_path) as connection:
        assert connection.execute(
            "SELECT current_boot_id FROM v3_mqtt_device_cursors"
        ).fetchone()[0] == BOOT_B


def test_boot_initial_sequence_and_uptime_must_be_plausible(mqtt_state):
    service, ingestor, _clock = mqtt_state
    assert ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(boot_id=BOOT_A, sequence=1, uptime_ms=1000),
        received_at=START,
    ).accepted

    uptime_regression = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(boot_id=BOOT_A, sequence=2, uptime_ms=900),
        received_at=START + timedelta(seconds=1),
    )
    bad_new_boot = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(boot_id=BOOT_B, sequence=8, uptime_ms=100),
        received_at=START + timedelta(seconds=2),
    )

    assert uptime_regression.code == "uptime_regression"
    assert bad_new_boot.code == "invalid_initial_sequence"
    assert _observation_count(service) == 1


def test_rejected_replay_does_not_refresh_offline_device(mqtt_state):
    service, ingestor, clock = mqtt_state
    accepted = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(sequence=1),
        received_at=START,
    )
    assert accepted.accepted

    clock.set(START + timedelta(seconds=31))
    assert service.get_device_state("camera-01")["connection_status"] == "offline"
    rejected = ingestor.ingest(
        topic="community/camera-01/status",
        payload=_payload(sequence=1),
        received_at=START + timedelta(seconds=32),
    )

    assert rejected.code == "duplicate_sequence"
    with sqlite3.connect(service.database_path) as connection:
        current = connection.execute(
            "SELECT connection_status, last_received_at "
            "FROM v3_device_current_state WHERE device_id = 'camera-01'"
        ).fetchone()
    assert current == ("offline", "2026-09-19T08:00:00Z")
    assert _observation_count(service) == 1


def test_migration_v1_checksum_is_frozen_and_v2_remains_registered(tmp_path):
    database_path = tmp_path / "migrations.sqlite"
    assert V3_MIGRATIONS[0].checksum == MIGRATION_V1_CHECKSUM
    assert V3_MIGRATIONS[1].version == 2

    initialize_v3_database(database_path)
    initialize_v3_database(database_path)

    with sqlite3.connect(database_path) as connection:
        versions = connection.execute(
            "SELECT version FROM v3_schema_migrations ORDER BY version"
        ).fetchall()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        observation_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(v3_device_state_observations)"
            )
        }
    assert versions == [
        (1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,),
    ]
    assert V3_MQTT_HEARTBEAT_TABLES <= tables
    assert {"boot_id", "firmware_version", "uptime_ms"} <= observation_columns
