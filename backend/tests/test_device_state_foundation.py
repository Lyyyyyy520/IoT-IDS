from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from services.device_state import (
    DeviceIdentityConflictError,
    DeviceStateService,
)
from v3_database import MIGRATION_TABLE, V3_DEVICE_STATE_TABLES, initialize_v3_database


class FakeClock:
    def __init__(self, current: datetime):
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def set(self, current: datetime) -> None:
        self.current = current

    def advance(self, **delta) -> None:
        self.current += timedelta(**delta)


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc))


@pytest.fixture
def state_service(tmp_path, clock):
    service = DeviceStateService(tmp_path / "phase1-test.sqlite", clock=clock)
    service.initialize()
    return service


def _bind_camera(service: DeviceStateService):
    return service.bind_device(
        device_id="camera-01",
        identity_kind="mac",
        identity_value="aa-bb-cc-dd-ee-01",
        display_name="客厅摄像头",
        device_type="camera",
        area_id="building-a",
    )


def _observe_camera(service: DeviceStateService, ip_address="192.168.4.11", sequence=1):
    return service.record_observation(
        device_id="camera-01",
        identity_kind="mac",
        identity_value="AA:BB:CC:DD:EE:01",
        source="probe-a",
        ip_address=ip_address,
        sequence=sequence,
    )


def test_schema_is_additive_and_repeated_initialization_is_idempotent(tmp_path):
    database_path = tmp_path / "coexist.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_marker VALUES ('preserved')")

    initialize_v3_database(database_path)
    initialize_v3_database(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        legacy_value = connection.execute("SELECT value FROM legacy_marker").fetchone()[0]
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM v3_schema_migrations"
        ).fetchone()[0]
    assert V3_DEVICE_STATE_TABLES <= tables
    assert MIGRATION_TABLE in tables
    assert legacy_value == "preserved"
    assert migration_count == 9


def test_stable_device_id_binds_one_immutable_identity(state_service):
    first = _bind_camera(state_service)
    second = _bind_camera(state_service)

    assert first["device_id"] == second["device_id"] == "camera-01"
    assert second["identity_value"] == "AA:BB:CC:DD:EE:01"

    with pytest.raises(DeviceIdentityConflictError):
        state_service.bind_device(
            device_id="camera-01",
            identity_kind="mac",
            identity_value="AA:BB:CC:DD:EE:02",
            display_name="错误摄像头",
            device_type="camera",
        )
    with pytest.raises(DeviceIdentityConflictError):
        state_service.bind_device(
            device_id="camera-02",
            identity_kind="mac",
            identity_value="AA:BB:CC:DD:EE:01",
            display_name="重复摄像头",
            device_type="camera",
        )


def test_ip_changes_are_observations_not_device_identity(state_service, clock):
    _bind_camera(state_service)
    _observe_camera(state_service, "192.168.4.11", sequence=1)
    clock.advance(seconds=5)
    current = _observe_camera(state_service, "192.168.4.77", sequence=2)

    assert current["device_id"] == "camera-01"
    assert current["identity_value"] == "AA:BB:CC:DD:EE:01"
    assert current["ip_address"] == "192.168.4.77"

    with sqlite3.connect(state_service.database_path) as connection:
        observations = connection.execute(
            "SELECT ip_address FROM v3_device_state_observations "
            "WHERE device_id = ? ORDER BY observation_id",
            ("camera-01",),
        ).fetchall()
    assert observations == [("192.168.4.11",), ("192.168.4.77",)]


def test_observation_identity_must_match_bound_device(state_service):
    _bind_camera(state_service)
    with pytest.raises(DeviceIdentityConflictError):
        state_service.record_observation(
            device_id="camera-01",
            identity_kind="mac",
            identity_value="AA:BB:CC:DD:EE:99",
            source="probe-a",
            ip_address="192.168.4.11",
        )


def test_connection_timeouts_use_injected_clock_without_sleep(state_service, clock):
    started_at = clock.current
    _bind_camera(state_service)
    assert _observe_camera(state_service)["connection_status"] == "online"

    clock.set(started_at + timedelta(seconds=14, milliseconds=999))
    assert state_service.get_device_state("camera-01")["connection_status"] == "online"
    clock.set(started_at + timedelta(seconds=15))
    assert state_service.get_device_state("camera-01")["connection_status"] == "stale"
    clock.set(started_at + timedelta(seconds=29, milliseconds=999))
    assert state_service.get_device_state("camera-01")["connection_status"] == "stale"
    clock.set(started_at + timedelta(seconds=30))
    assert state_service.get_device_state("camera-01")["connection_status"] == "offline"


def test_maintenance_and_disabled_modes_suppress_offline_alert_only(state_service, clock):
    _bind_camera(state_service)
    _observe_camera(state_service)
    state_service.set_operation_mode("camera-01", "maintenance")
    clock.advance(seconds=31)

    maintenance = state_service.get_device_state("camera-01")
    assert maintenance["connection_status"] == "offline"
    assert maintenance["operation_mode"] == "maintenance"
    assert maintenance["offline_alert_eligible"] is False

    active = state_service.set_operation_mode("camera-01", "active")
    assert active["offline_alert_eligible"] is True
    disabled = state_service.set_operation_mode("camera-01", "disabled")
    assert disabled["connection_status"] == "offline"
    assert disabled["offline_alert_eligible"] is False


def test_component_restart_returns_to_warming_up_then_can_degrade(state_service, clock):
    ready = state_service.set_component_readiness("gnn-inference", "ready")
    assert ready["readiness"] == "ready"
    assert ready["ready_at"] is not None

    clock.advance(seconds=10)
    warming = state_service.restart_component("gnn-inference", "process restarted")
    assert warming["readiness"] == "warming_up"
    assert warming["ready_at"] is None
    assert warming["started_at"] != ready["started_at"]

    clock.advance(seconds=5)
    degraded = state_service.set_component_readiness(
        "gnn-inference", "degraded", "model unavailable"
    )
    assert degraded["readiness"] == "degraded"
    assert degraded["reason"] == "model unavailable"
    assert degraded["state_version"] == 3
