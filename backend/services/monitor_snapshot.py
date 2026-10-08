"""Real v3 monitor snapshots backed only by the explicit SQLite database."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Callable

from contracts import API_VERSION, SCHEMA_VERSION
from services.device_state import DeviceStateService
from services.incident_workflow import monitor_incident_snapshot
from services.realtime_events import (
    RealtimeEventStore,
    V3DatabaseUnavailable,
    current_event_cursor,
)


Clock = Callable[[], datetime]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise V3DatabaseUnavailable("monitor clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _planned_linkages() -> list[dict]:
    """Planned device linkages (CoAP 联动) from the community topology registry."""
    try:
        import importlib.util
        from pathlib import Path

        edge_dir = Path(__file__).resolve().parent.parent.parent / "edge"
        spec = importlib.util.spec_from_file_location(
            "iot_community_devices", edge_dir / "community_devices.py"
        )
        if spec is None or spec.loader is None:
            return []
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        PHYSICAL_DEVICES = module.PHYSICAL_DEVICES
        VIRTUAL_DEVICES = module.VIRTUAL_DEVICES

        ip_to_device: dict[str, str] = {}
        for device_id, _type, ip in PHYSICAL_DEVICES:
            ip_to_device[ip] = device_id
        for device_id, _type, ip, _coap, _links in VIRTUAL_DEVICES:
            ip_to_device[ip] = device_id

        edges: list[dict] = []
        for device_id, _type, ip, _coap, links in VIRTUAL_DEVICES:
            for target_ip in links:
                target = ip_to_device.get(target_ip)
                if target and target != device_id:
                    edges.append({
                        "source": device_id,
                        "target": target,
                        "bytes": 0,
                        "packets": 0,
                        "protocols": ["coap"],
                        "relation_type": "configured_linkage",
                    })
        return edges
    except Exception:
        return []


class MonitorSnapshotService:
    """Refresh timeout state, then read one consistent SQLite snapshot."""

    def __init__(
        self,
        database_path: str | Path | None,
        *,
        clock: Clock | None = None,
    ):
        self.database_path = Path(database_path) if database_path else None
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._events = RealtimeEventStore(self.database_path)
        self._states = (
            DeviceStateService(
                self.database_path,
                clock=self._clock,
                create_if_missing=False,
            )
            if self.database_path is not None
            else None
        )

    def _graph_snapshot(self, connection: sqlite3.Connection, now: datetime) -> dict:
        """Build a device graph snapshot: nodes (devices) + observed peer edges."""
        start = now - timedelta(hours=24)
        start_text, end_text = _iso(start), _iso(now)
        node_rows = connection.execute(
            "SELECT p.device_id, p.display_name, p.device_type, "
            "COALESCE(s.connection_status, 'unknown') AS connection_status, "
            "s.ip_address, s.risk_level, s.risk_name "
            "FROM v3_device_profiles p "
            "LEFT JOIN v3_device_current_state s ON s.device_id = p.device_id "
            "ORDER BY p.device_id"
        ).fetchall()
        nodes = [{
            "id": row["device_id"],
            "label": row["display_name"],
            "type": row["device_type"],
            "status": row["connection_status"],
            "risk_level": row["risk_level"],
            "risk_name": row["risk_name"],
            "ip": row["ip_address"],
        } for row in node_rows]

        node_ids = {node["id"] for node in nodes}

        edges: list[dict] = []
        traffic_ready = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='v3_device_traffic_peer_minutes'"
        ).fetchone() is not None
        if traffic_ready:
            edge_rows = connection.execute(
                "SELECT t.device_id AS source, t.peer_device_id AS target, "
                "SUM(t.bytes) AS bytes, SUM(t.packets) AS packets, "
                "GROUP_CONCAT(DISTINCT t.protocol) AS protocols "
                "FROM v3_device_traffic_peer_minutes t "
                "WHERE t.direction='tx' AND t.peer_device_id IS NOT NULL "
                "AND t.peer_device_id<>t.device_id "
                "AND t.bucket_start>=? AND t.bucket_start<? "
                "GROUP BY t.device_id, t.peer_device_id "
                "ORDER BY t.device_id, t.peer_device_id",
                (start_text, end_text),
            ).fetchall()
            edges = [{
                "source": row["source"],
                "target": row["target"],
                "bytes": int(row["bytes"]),
                "packets": int(row["packets"]),
                "protocols": sorted(row["protocols"].split(",")) if row["protocols"] else [],
                "relation_type": "observed_traffic",
            } for row in edge_rows]

        # Merge planned community linkages (联动) not already covered by observed traffic.
        observed_pairs = {(e["source"], e["target"]) for e in edges}
        for planned in _planned_linkages():
            if planned["source"] not in node_ids or planned["target"] not in node_ids:
                continue
            pair = (planned["source"], planned["target"])
            if pair not in observed_pairs and (pair[1], pair[0]) not in observed_pairs:
                edges.append(planned)

        return {
            "window": {"from": start_text, "to": end_text, "range": "24h"},
            "nodes": nodes,
            "edges": edges,
            "availability": {
                "source": "community_topology",
                "reason": None if edges else "no_observed_device_relationships",
            },
        }

    def snapshot(self) -> dict:
        if self._states is None:
            raise V3DatabaseUnavailable("monitor database path is not configured")
        try:
            with self._events.connection():
                pass
            self._states.refresh_connection_statuses()
            now = self._clock()
            generated_at = _iso(now)
            with self._events.connection() as connection:
                connection.execute("BEGIN")
                device_rows = connection.execute(
                    "SELECT p.device_id, p.display_name, p.device_type, p.area_id, "
                    "p.operation_mode, c.connection_status, c.ip_address, "
                    "c.state_version, c.last_observed_at, c.last_received_at, "
                    "c.risk_level, c.risk_name, c.risk_confidence, c.risk_detected_at "
                    "FROM v3_device_profiles p "
                    "JOIN v3_device_current_state c ON c.device_id = p.device_id "
                    "ORDER BY p.device_id"
                ).fetchall()
                source_rows = connection.execute(
                    "SELECT DISTINCT device_id, source "
                    "FROM v3_device_state_observations "
                    "ORDER BY device_id, source"
                ).fetchall()
                component_rows = connection.execute(
                    "SELECT component_id, readiness, started_at, ready_at, reason, "
                    "state_version, updated_at FROM v3_system_component_health "
                    "ORDER BY component_id"
                ).fetchall()
                event_cursor = current_event_cursor(connection)
                incident_snapshot = monitor_incident_snapshot(
                    connection
                )
                graph = self._graph_snapshot(connection, now)
                connection.commit()
        except V3DatabaseUnavailable:
            raise
        except (FileNotFoundError, sqlite3.Error) as exc:
            raise V3DatabaseUnavailable("monitor database is unavailable") from exc

        sources_by_device: dict[str, list[str]] = {}
        for row in source_rows:
            sources_by_device.setdefault(row["device_id"], []).append(row["source"])
        devices = [
            {
                "device_id": row["device_id"],
                "display_name": row["display_name"],
                "device_type": row["device_type"],
                "area_id": row["area_id"],
                "operation_mode": row["operation_mode"],
                "connection_status": row["connection_status"],
                "ip_address": row["ip_address"],
                "state_version": row["state_version"],
                "observed_at": row["last_observed_at"],
                "received_at": row["last_received_at"],
                "risk_level": row["risk_level"],
                "risk_name": row["risk_name"],
                "risk_confidence": row["risk_confidence"],
                "risk_detected_at": row["risk_detected_at"],
                "sources": sources_by_device.get(row["device_id"], []),
            }
            for row in device_rows
        ]
        components = [dict(row) for row in component_rows]
        return {
            "api_version": API_VERSION,
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "event_cursor": event_cursor,
            "devices": devices,
            "system_components": components,
            "capabilities": {
                "graph": {
                    "available": True,
                    "reason": None,
                },
                "incident": {
                    **incident_snapshot["capability"],
                },
            },
            "graph": graph,
            "incidents": incident_snapshot["data"],
        }


__all__ = ["MonitorSnapshotService"]
