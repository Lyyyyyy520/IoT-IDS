from contracts import (
    ConnectionStatus,
    CURRENT_MODEL_EDGE_CAPABILITY,
    DetectionReadiness,
    DEVICE_FEATURE_NAMES,
    DEVICE_FEATURE_VERSION,
    GNN_WINDOW_SECONDS,
    GRAPH_CONTRACT_VERSION,
    HEARTBEAT_INTERVAL_SECONDS,
    IncidentRole,
    IncidentStage,
    ModelEdgeCapability,
    OFFLINE_AFTER_SECONDS,
    OperationMode,
    REALTIME_EVENT_TYPES,
    Role,
    SecurityStatus,
    STALE_AFTER_SECONDS,
    enum_values,
    is_isolated_lab_target,
    is_valid_device_id,
)


def test_status_and_role_contracts_are_frozen():
    assert enum_values(Role) == ("admin", "operator", "user")
    assert enum_values(ConnectionStatus) == ("unknown", "online", "stale", "offline")
    assert enum_values(SecurityStatus) == (
        "unknown", "safe", "suspicious", "under_attack", "compromised", "isolated"
    )
    assert enum_values(OperationMode) == ("active", "maintenance", "disabled")
    assert enum_values(DetectionReadiness) == ("warming_up", "ready", "degraded")
    assert enum_values(IncidentRole) == (
        "affected", "suspected_source", "observer", "unknown"
    )
    assert enum_values(IncidentStage) == (
        "open", "acknowledged", "recovering", "resolved", "false_positive"
    )


def test_default_timing_and_model_contracts():
    assert HEARTBEAT_INTERVAL_SECONDS == 5
    assert STALE_AFTER_SECONDS == 15
    assert OFFLINE_AFTER_SECONDS == 30
    assert GNN_WINDOW_SECONDS == 60
    assert STALE_AFTER_SECONDS < OFFLINE_AFTER_SECONDS
    assert len(DEVICE_FEATURE_NAMES) == 13
    assert GRAPH_CONTRACT_VERSION == "device-graph-v1"
    assert DEVICE_FEATURE_VERSION == "device-13-v1"
    assert CURRENT_MODEL_EDGE_CAPABILITY == ModelEdgeCapability.BINARY_ADJACENCY.value


def test_realtime_event_names_are_unique():
    assert len(REALTIME_EVENT_TYPES) == len(set(REALTIME_EVENT_TYPES))


def test_device_id_is_topic_safe():
    assert is_valid_device_id("door-01")
    assert is_valid_device_id("camera_a02")
    assert not is_valid_device_id("Door 01")
    assert not is_valid_device_id("x")
    assert not is_valid_device_id("../door-01")


def test_lab_target_must_be_private_and_inside_explicit_cidr():
    allowed = ("192.168.4.0/24",)
    assert is_isolated_lab_target("192.168.4.200", allowed)
    assert not is_isolated_lab_target("8.8.8.8", allowed)
    assert not is_isolated_lab_target("192.168.5.20", allowed)
    assert not is_isolated_lab_target("192.168.4.255", allowed)
    assert not is_isolated_lab_target("127.0.0.1", allowed)
