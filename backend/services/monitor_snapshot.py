"""Real v3 monitor snapshots backed only by the explicit SQLite database."""
from datetime import datetime, timezone
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

    def snapshot(self) -> dict:
        if self._states is None:
            raise V3DatabaseUnavailable("monitor database path is not configured")
        try:
            with self._events.connection():
                pass
            self._states.refresh_connection_statuses()
            generated_at = _iso(self._clock())
            with self._events.connection() as connection:
                connection.execute("BEGIN")
                device_rows = connection.execute(
                    "SELECT p.device_id, p.display_name, p.device_type, p.area_id, "
                    "p.operation_mode, c.connection_status, c.ip_address, "
                    "c.state_version, c.last_observed_at, c.last_received_at "
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
                    "available": False,
                    "reason": "graph_snapshots_not_implemented",
                },
                "incident": {
                    **incident_snapshot["capability"],
                },
            },
            "incidents": incident_snapshot["data"],
        }


__all__ = ["MonitorSnapshotService"]
