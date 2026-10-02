"""Stable v3 domain contracts shared by backend services and tests.

This module is deliberately independent from Flask and SQLite.  Phase 0 uses
it to freeze names and defaults before any real database migration is run.
"""
from enum import Enum
import ipaddress
import re
from typing import Iterable


API_VERSION = "v3"
SCHEMA_VERSION = 4
MQTT_HEARTBEAT_SCHEMA_VERSION = 2
MQTT_HEARTBEAT_MAX_BYTES = 4096
MQTT_TELEMETRY_MAX_BYTES = 2048
MQTT_BOOT_ID_HEX_LENGTH = 32
MQTT_INITIAL_SEQUENCE_VALUES = (0, 1)

HEARTBEAT_INTERVAL_SECONDS = 5
STALE_AFTER_SECONDS = 15
OFFLINE_AFTER_SECONDS = 30
GNN_WINDOW_SECONDS = 60
SAFE_WINDOWS_TO_RECOVER = 3

DEVICE_FEATURE_NAMES = (
    "flow_count",
    "total_packets",
    "total_bytes",
    "avg_packets",
    "avg_bytes",
    "max_packets",
    "unique_dst_ports",
    "unique_dst_ips",
    "unique_src_ports",
    "unique_protocols",
    "tcp_flags_mean",
    "avg_flow_duration",
    "internal_ratio",
)

REALTIME_EVENT_TYPES = (
    "snapshot.required",
    "device.discovered",
    "device.connection_changed",
    "device.telemetry_updated",
    "device.inventory_changed",
    "device.security_changed",
    "graph.snapshot_created",
    "graph.node_state_changed",
    "graph.inference_completed",
    "incident.opened",
    "incident.updated",
    "incident.recovering",
    "incident.resolved",
    "system.component_changed",
)


class StringEnum(str, Enum):
    """Python 3.9-compatible string enum."""

    def __str__(self) -> str:
        return self.value


class Role(StringEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    USER = "user"


class ConnectionStatus(StringEnum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    STALE = "stale"
    OFFLINE = "offline"


class SecurityStatus(StringEnum):
    UNKNOWN = "unknown"
    SAFE = "safe"
    SUSPICIOUS = "suspicious"
    UNDER_ATTACK = "under_attack"
    COMPROMISED = "compromised"
    ISOLATED = "isolated"


class OperationMode(StringEnum):
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    DISABLED = "disabled"


class DetectionReadiness(StringEnum):
    WARMING_UP = "warming_up"
    READY = "ready"
    DEGRADED = "degraded"


class IncidentRole(StringEnum):
    AFFECTED = "affected"
    SUSPECTED_SOURCE = "suspected_source"
    OBSERVER = "observer"
    UNKNOWN = "unknown"


class IncidentStage(StringEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RECOVERING = "recovering"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"


class DeviceLifecycle(StringEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class DeviceImportance(StringEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class DeviceProfileSource(StringEnum):
    UNCLASSIFIED = "unclassified"
    PHYSICAL = "physical"
    VIRTUAL = "virtual"
    GATEWAY = "gateway"


class ModelEdgeCapability(StringEnum):
    BINARY_ADJACENCY = "binary_adjacency"
    EDGE_FEATURES = "edge_features"


GRAPH_CONTRACT_VERSION = "device-graph-v1"
DEVICE_FEATURE_VERSION = "device-13-v1"
CURRENT_MODEL_EDGE_CAPABILITY = ModelEdgeCapability.BINARY_ADJACENCY.value


_DEVICE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


def is_valid_device_id(value: str) -> bool:
    """Return whether a firmware device id is stable and topic-safe."""
    return bool(_DEVICE_ID_PATTERN.fullmatch(value or ""))


def enum_values(enum_type: type[StringEnum]) -> tuple[str, ...]:
    return tuple(member.value for member in enum_type)


def is_isolated_lab_target(target: str, allowed_cidrs: Iterable[str]) -> bool:
    """Allow attack simulation only for unicast addresses in explicit private CIDRs."""
    try:
        address = ipaddress.ip_address(target)
    except ValueError:
        return False

    if not address.is_private or address.is_loopback or address.is_multicast:
        return False

    for raw_cidr in allowed_cidrs:
        try:
            network = ipaddress.ip_network(raw_cidr, strict=False)
        except ValueError:
            continue
        if address in network and address not in (network.network_address, network.broadcast_address):
            return True
    return False
