"""Strictly scoped mobile device summaries and simplified traffic views."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Callable

from services.device_traffic import (
    DeviceTrafficService,
    TrafficStoreUnavailable,
    ensure_traffic_schema,
)
from services.mobile_access import (
    MobileAccessError,
    MobileAccessService,
    MobilePrincipal,
    MobileStoreUnavailable,
)
from v3_database import (
    V3_DEVICE_TRAFFIC_MIGRATION,
    V3_INCIDENT_WORKFLOW_MIGRATION,
    V3_MOBILE_USER_ADMIN_MIGRATION,
    V3_REALTIME_EVENT_MIGRATION,
    connect_v3_existing,
    read_applied_migrations,
)


class MobileDeviceUnavailable(MobileAccessError):
    code = "mobile_device_unavailable"
    status = 404


class MobileDeviceTrafficUnavailable(MobileAccessError):
    code = "mobile_traffic_unavailable"
    status = 503


class MobileDeviceTrafficWindowError(MobileAccessError):
    code = "invalid_mobile_traffic_window"
    status = 400


class MobileDeviceHistoryWindowError(MobileAccessError):
    code = "invalid_mobile_history_window"
    status = 400


class MobileTopologyWindowError(MobileAccessError):
    code = "invalid_mobile_topology_window"
    status = 400


_WINDOWS = {
    "15m": (timedelta(minutes=15), 60),
    "1h": (timedelta(hours=1), 300),
    "24h": (timedelta(hours=24), 3600),
}
_HISTORY_WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
_TOPOLOGY_WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
_STALE_SECONDS = 120
_ACTIVE_INCIDENT_STATUSES = ("open", "acknowledged", "recovering")


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return an aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _bucket_iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _status_label(connection_status: str, operation_mode: str, retired: bool) -> dict[str, str]:
    connection = {
        "online": "设备最近有连接记录",
        "stale": "设备连接状态可能有延迟",
        "offline": "设备暂未在线",
        "unknown": "尚无可用的连接记录",
    }.get(connection_status, "连接状态暂不可用")
    operation = {
        "active": "正常运行模式",
        "maintenance": "维护模式",
        "disabled": "已停用",
    }.get(operation_mode, "运行模式暂不可用")
    if retired:
        operation = "已退役"
    return {"connection": connection, "operation": operation}


def _availability(connection_status: str, operation_mode: str, retired: bool) -> tuple[str, str]:
    if retired:
        return "retired", "设备已退役，以下流量仅供查看历史记录"
    if operation_mode == "maintenance":
        return "maintenance", "设备处于维护模式"
    if operation_mode == "disabled":
        return "disabled", "设备已停用"
    return {
        "online": ("available", "设备最近有连接记录"),
        "stale": ("delayed", "设备连接状态可能有延迟"),
        "offline": ("unavailable", "设备暂未在线"),
        "unknown": ("unknown", "尚无可用的连接记录"),
    }.get(connection_status, ("unknown", "连接状态暂不可用"))


class MobileDeviceReadService:
    """Read mobile DTOs directly from allowlisted v3 facts after live scope checks."""

    def __init__(
        self,
        mobile_access: MobileAccessService,
        traffic: DeviceTrafficService,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.mobile_access = mobile_access
        self.traffic_service = traffic
        self.database_path: Path | None = mobile_access.database_path
        self.clock = clock or mobile_access.clock

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return an aware datetime")
        return value.astimezone(timezone.utc)

    def _connection(self) -> sqlite3.Connection:
        if self.database_path is None:
            raise MobileStoreUnavailable("mobile database path is not configured")
        try:
            connection = connect_v3_existing(self.database_path)
            migrations = {row["version"]: row for row in read_applied_migrations(connection)}
            required = migrations.get(V3_MOBILE_USER_ADMIN_MIGRATION.version)
            if (
                required is None
                or required["name"] != V3_MOBILE_USER_ADMIN_MIGRATION.name
                or required["checksum"] != V3_MOBILE_USER_ADMIN_MIGRATION.checksum
            ):
                connection.close()
                raise MobileStoreUnavailable("mobile device schema is unavailable")
            return connection
        except (FileNotFoundError, sqlite3.Error) as exc:
            raise MobileStoreUnavailable("mobile device database is unavailable") from exc

    @staticmethod
    def _migration_available(connection: sqlite3.Connection, migration) -> bool:
        try:
            row = connection.execute(
                "SELECT name, checksum FROM v3_schema_migrations WHERE version=?",
                (migration.version,),
            ).fetchone()
        except sqlite3.Error:
            return False
        return bool(
            row
            and row["name"] == migration.name
            and row["checksum"] == migration.checksum
        )

    @staticmethod
    def _scoped_device(
        connection: sqlite3.Connection,
        principal: MobilePrincipal,
        device_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT p.device_id,p.display_name,p.device_type,p.area_id,p.operation_mode,"
            "p.retired_at,p.updated_at AS profile_updated_at,"
            "COALESCE(s.connection_status,'unknown') AS connection_status,"
            "s.last_received_at,s.updated_at AS state_updated_at "
            "FROM v3_device_profiles p LEFT JOIN v3_device_current_state s "
            "ON s.device_id=p.device_id WHERE p.device_id=? AND EXISTS ("
            "SELECT 1 FROM v3_mobile_user_scopes scope WHERE scope.user_id=? "
            "AND scope.revoked_at IS NULL AND ((scope.scope_kind='device' "
            "AND scope.scope_value=p.device_id) OR (scope.scope_kind='area' "
            "AND scope.scope_value=p.area_id)))",
            (device_id, principal.user_id),
        ).fetchone()
        if row is None:
            # Identical response for a missing device and a device outside scope.
            raise MobileDeviceUnavailable("设备不可用或不在当前授权范围")
        return row

    @staticmethod
    def _security_summary(
        connection: sqlite3.Connection,
        principal: MobilePrincipal,
        device_id: str,
        available: bool,
    ) -> dict:
        if not available:
            return {
                "available": False,
                "reason": "incident_pipeline_not_ready",
                "active_notice_count": None,
                "recent_notices": [],
                "gnn": {"available": False, "reason": "gnn_capability_unavailable"},
            }
        count = int(connection.execute(
            "SELECT COUNT(DISTINCT i.incident_id) "
            "FROM v3_incidents i JOIN v3_incident_devices d "
            "ON d.incident_id=i.incident_id JOIN v3_device_profiles p "
            "ON p.device_id=d.device_id JOIN v3_mobile_user_scopes scope "
            "ON scope.user_id=? AND scope.revoked_at IS NULL AND "
            "((scope.scope_kind='device' AND scope.scope_value=d.device_id) OR "
            "(scope.scope_kind='area' AND scope.scope_value=p.area_id)) "
            "WHERE d.device_id=? AND d.incident_role='affected' AND d.user_visible=1 "
            "AND i.mobile_published=1 AND i.status IN ('open','acknowledged','recovering')",
            (principal.user_id, device_id),
        ).fetchone()[0])
        rows = connection.execute(
            "SELECT DISTINCT i.incident_id,i.user_title,i.severity,i.status,i.updated_at,"
            "ack.first_read_at,ack.acknowledged_at "
            "FROM v3_incidents i JOIN v3_incident_devices d "
            "ON d.incident_id=i.incident_id JOIN v3_device_profiles p "
            "ON p.device_id=d.device_id JOIN v3_mobile_user_scopes scope "
            "ON scope.user_id=? AND scope.revoked_at IS NULL AND "
            "((scope.scope_kind='device' AND scope.scope_value=d.device_id) OR "
            "(scope.scope_kind='area' AND scope.scope_value=p.area_id)) "
            "LEFT JOIN v3_mobile_notice_acknowledgements ack "
            "ON ack.incident_id=i.incident_id AND ack.user_id=? "
            "WHERE d.device_id=? AND d.incident_role='affected' AND d.user_visible=1 "
            "AND i.mobile_published=1 AND i.status IN ('open','acknowledged','recovering') "
            "ORDER BY i.updated_at DESC,i.incident_id LIMIT 3",
            (principal.user_id, principal.user_id, device_id),
        ).fetchall()
        recent = [{
            "incident_id": row["incident_id"],
            "user_title": row["user_title"],
            "severity": row["severity"],
            "status": row["status"],
            "updated_at": row["updated_at"],
            "read": row["first_read_at"] is not None,
            "acknowledged": row["acknowledged_at"] is not None,
        } for row in rows]
        return {
            "available": True,
            "reason": None,
            "active_notice_count": count,
            "recent_notices": recent,
            "gnn": {"available": False, "reason": "gnn_capability_unavailable"},
        }

    def detail(self, principal: MobilePrincipal, device_id: str) -> dict:
        connection = self._connection()
        try:
            row = self._scoped_device(connection, principal, device_id)
            retired = row["retired_at"] is not None
            availability, availability_text = _availability(
                row["connection_status"], row["operation_mode"], retired
            )
            traffic_ready = self._migration_available(
                connection, V3_DEVICE_TRAFFIC_MIGRATION
            )
            incidents_ready = self._migration_available(
                connection, V3_INCIDENT_WORKFLOW_MIGRATION
            )
            security = self._security_summary(
                connection, principal, device_id, incidents_ready
            )
            return {
                "device_id": row["device_id"],
                "display_name": row["display_name"],
                "device_type": row["device_type"],
                "area_id": row["area_id"],
                "connection_status": row["connection_status"],
                "operation_mode": row["operation_mode"],
                "retired": retired,
                "retired_at": row["retired_at"],
                "last_updated_at": row["state_updated_at"] or row["profile_updated_at"],
                "last_seen_at": row["last_received_at"],
                "availability_status": availability,
                "status_text": _status_label(
                    row["connection_status"], row["operation_mode"], retired
                ),
                "availability_text": availability_text,
                "security_capability": security,
                "traffic_capability": {
                    "available": traffic_ready,
                    "reason": None if traffic_ready else "traffic_pipeline_not_ready",
                },
            }
        except sqlite3.Error as exc:
            raise MobileStoreUnavailable("mobile device data is unavailable") from exc
        finally:
            connection.close()

    @staticmethod
    def _safe_telemetry(payload_json: str | None) -> dict:
        """Keep only public scalar telemetry fields from retained observations."""
        if not payload_json:
            return {}
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        telemetry = payload.get("telemetry")
        if not isinstance(telemetry, dict):
            telemetry = payload
        omitted_fields = {
            "device_type", "device_id", "boot_id", "sequence", "schema_version",
            "firmware_version", "uptime_ms", "ip", "mac", "ip_address", "mac_address",
            "src_ip", "dst_ip", "peer_ip", "src_mac", "dst_mac", "peer_mac",
        }
        sensitive_fragments = (
            "password", "passwd", "token", "secret", "credential",
            "auth", "jwt", "api_key", "apikey", "signature",
        )
        safe = {}
        for key, value in telemetry.items():
            if not isinstance(key, str) or not key or len(key) > 64:
                continue
            normalized = key.casefold()
            if (normalized in omitted_fields or normalized.endswith(("_ip", "_mac")) or any(
                fragment in normalized for fragment in sensitive_fragments
            )):
                continue
            if isinstance(value, str) and len(value) <= 256:
                safe[key] = value
            elif value is None or isinstance(value, bool) or isinstance(value, int):
                safe[key] = value
            elif isinstance(value, float) and math.isfinite(value):
                safe[key] = value
        return safe

    def history(
        self, principal: MobilePrincipal, device_id: str, window: str,
    ) -> dict:
        """Return scoped, allowlisted retained telemetry and connection changes."""
        duration = _HISTORY_WINDOWS.get(window)
        if duration is None:
            raise MobileDeviceHistoryWindowError(
                "只支持最近 1 小时、24 小时或 7 天"
            )
        connection = self._connection()
        try:
            connection.execute("BEGIN")
            self._scoped_device(connection, principal, device_id)
            now = self._now()
            start, end = now - duration, now
            start_text, end_text = _iso(start), _iso(end)
            count = int(connection.execute(
                "SELECT COUNT(*) FROM v3_device_state_observations "
                "WHERE device_id=? AND received_at>=? AND received_at<=?",
                (device_id, start_text, end_text),
            ).fetchone()[0])
            # Select a real retained sample from each time bucket. The bounded
            # response preserves gaps instead of synthesizing zero values.
            bucket_seconds = max(1, math.ceil(duration.total_seconds() / 500))
            rows = connection.execute(
                "WITH ranked AS (SELECT observation_id,observed_at,received_at,payload_json,"
                "ROW_NUMBER() OVER (PARTITION BY CAST(strftime('%s',received_at) AS INTEGER)/? "
                "ORDER BY received_at DESC,observation_id DESC) AS sample_rank "
                "FROM v3_device_state_observations WHERE device_id=? AND received_at>=? AND received_at<=?) "
                "SELECT observation_id,observed_at,received_at,payload_json FROM ranked "
                "WHERE sample_rank=1 ORDER BY received_at,observation_id LIMIT 501",
                (bucket_seconds, device_id, start_text, end_text),
            ).fetchall()
            latest_row = connection.execute(
                "SELECT observation_id,observed_at,received_at,payload_json "
                "FROM v3_device_state_observations WHERE device_id=? "
                "ORDER BY received_at DESC,observation_id DESC LIMIT 1",
                (device_id,),
            ).fetchone()

            status_available = self._migration_available(
                connection, V3_REALTIME_EVENT_MIGRATION
            )
            status_changes = []
            status_truncated = False
            if status_available:
                status_rows = connection.execute(
                    "SELECT event_id,occurred_at,payload_json FROM v3_realtime_events "
                    "WHERE event_type='device.connection_changed' AND device_id=? "
                    "AND occurred_at>=? AND occurred_at<=? "
                    "ORDER BY occurred_at,event_id LIMIT 501",
                    (device_id, start_text, end_text),
                ).fetchall()
                status_truncated = len(status_rows) > 500
                valid_statuses = {"unknown", "online", "stale", "offline"}
                for item in status_rows[:500]:
                    try:
                        event = json.loads(item["payload_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        event = {}
                    previous = event.get("from") if isinstance(event, dict) else None
                    current = event.get("to") if isinstance(event, dict) else None
                    if previous not in valid_statuses or current not in valid_statuses:
                        continue
                    status_changes.append({
                        "event_id": int(item["event_id"]),
                        "occurred_at": item["occurred_at"],
                        "from_status": previous,
                        "to_status": current,
                        "source": "timeout" if event.get("source") == "timeout" else "report",
                    })

            def point(row):
                return {
                    "observation_id": int(row["observation_id"]),
                    "observed_at": row["observed_at"],
                    "received_at": row["received_at"],
                    "telemetry": self._safe_telemetry(row["payload_json"]),
                }

            series = [point(item) for item in rows[:500]]
            latest = point(latest_row) if latest_row is not None else None
            has_telemetry = any(item["telemetry"] for item in series)
            reason = None if has_telemetry else (
                "no_state_observations" if count == 0 else "no_telemetry_fields"
            )
            return {
                "device_id": device_id,
                "window": window,
                "query_window": {"from": start_text, "to": end_text},
                "generated_at": _iso(now),
                "availability": {"available": has_telemetry, "reason": reason},
                "data_source": "v3_device_state_observations",
                "latest": latest,
                "series": series,
                "truncated": count > len(series),
                "bucket_seconds": bucket_seconds,
                "connection_history_available": status_available,
                "connection_status_changes": status_changes,
                "connection_history_truncated": status_truncated,
                "connection_history_note": (
                    "状态变化只在服务端刷新连接状态时记录，并受事件保留期影响。"
                    if status_available else "连接状态历史尚未配置。"
                ),
            }
        except sqlite3.Error as exc:
            raise MobileStoreUnavailable("mobile device history is unavailable") from exc
        finally:
            connection.close()

    def topology(self, principal: MobilePrincipal, window: str) -> dict:
        """Return device nodes and observed device-pair edges within live user scope."""
        duration = _TOPOLOGY_WINDOWS.get(window)
        if duration is None:
            raise MobileTopologyWindowError(
                "只支持最近 1 小时、24 小时或 7 天"
            )
        connection = self._connection()
        try:
            connection.execute("BEGIN")
            now = self._now()
            start, end = now - duration, now
            start_text, end_text = _iso(start), _iso(end)
            nodes = connection.execute(
                "SELECT p.device_id,p.display_name,p.device_type,p.area_id,p.retired_at,"
                "COALESCE(s.connection_status,'unknown') AS connection_status,s.last_received_at "
                "FROM v3_device_profiles p LEFT JOIN v3_device_current_state s ON s.device_id=p.device_id "
                "WHERE EXISTS (SELECT 1 FROM v3_mobile_user_scopes scope WHERE scope.user_id=? "
                "AND scope.revoked_at IS NULL AND ((scope.scope_kind='device' AND scope.scope_value=p.device_id) "
                "OR (scope.scope_kind='area' AND scope.scope_value=p.area_id))) ORDER BY p.device_id",
                (principal.user_id,),
            ).fetchall()
            scoped_ids = {item["device_id"] for item in nodes}
            traffic_ready = self._migration_available(
                connection, V3_DEVICE_TRAFFIC_MIGRATION
            )
            incident_ready = self._migration_available(
                connection, V3_INCIDENT_WORKFLOW_MIGRATION
            )
            alerts_by_device: dict[str, list[str]] = {}
            if incident_ready and scoped_ids:
                incident_rows = connection.execute(
                    "SELECT d.device_id,i.severity FROM v3_incident_devices d "
                    "JOIN v3_incidents i ON i.incident_id=d.incident_id "
                    "WHERE d.device_id IN (" + ",".join("?" for _ in scoped_ids) + ") "
                    "AND d.incident_role='affected' AND d.user_visible=1 AND i.mobile_published=1 "
                    "AND i.status IN ('open','acknowledged','recovering') "
                    "ORDER BY d.device_id,CASE i.severity WHEN 'critical' THEN 5 WHEN 'high' THEN 4 "
                    "WHEN 'medium' THEN 3 WHEN 'low' THEN 2 ELSE 1 END DESC",
                    sorted(scoped_ids),
                ).fetchall()
                for item in incident_rows:
                    alerts_by_device.setdefault(item["device_id"], []).append(item["severity"])

            edges = []
            if traffic_ready and scoped_ids:
                ids = sorted(scoped_ids)
                placeholders = ",".join("?" for _ in ids)
                edge_rows = connection.execute(
                    "SELECT t.device_id AS source_device_id,t.peer_device_id AS target_device_id,"
                    "SUM(t.bytes) AS bytes,SUM(t.packets) AS packets,SUM(t.flow_count) AS flows,"
                    "GROUP_CONCAT(DISTINCT t.protocol) AS protocols,MAX(t.last_sample_at) AS last_seen "
                    "FROM v3_device_traffic_peer_minutes t "
                    "WHERE t.direction='tx' AND t.peer_device_id IS NOT NULL "
                    "AND t.peer_device_id<>t.device_id AND t.device_id IN (" + placeholders + ") "
                    "AND t.peer_device_id IN (" + placeholders + ") "
                    "AND t.bucket_start>=? AND t.bucket_start<? "
                    "GROUP BY t.device_id,t.peer_device_id ORDER BY t.device_id,t.peer_device_id",
                    (*ids, *ids, start_text, end_text),
                ).fetchall()
                edges = [{
                    "source_device_id": row["source_device_id"],
                    "target_device_id": row["target_device_id"],
                    "bytes": int(row["bytes"]), "packets": int(row["packets"]),
                    "flows": int(row["flows"]),
                    "protocols": sorted(row["protocols"].split(",")) if row["protocols"] else [],
                    "last_seen": row["last_seen"],
                } for row in edge_rows]
            return {
                "generated_at": _iso(now),
                "window": {"from": start_text, "to": end_text, "range": window},
                "nodes": [{
                    "device_id": row["device_id"], "display_name": row["display_name"],
                    "device_type": row["device_type"], "area_id": row["area_id"],
                    "connection_status": row["connection_status"],
                    "last_received_at": row["last_received_at"],
                    "retired": row["retired_at"] is not None,
                    "active_alert_count": len(alerts_by_device.get(row["device_id"], [])) if incident_ready else None,
                    "highest_alert_severity": alerts_by_device[row["device_id"]][0]
                    if incident_ready and alerts_by_device.get(row["device_id"]) else None,
                } for row in nodes],
                "edges": edges,
                "availability": {
                    "available": traffic_ready,
                    "reason": (
                        "device_traffic_aggregation_not_ready" if not traffic_ready
                        else "no_observed_device_relationships" if not edges else None
                    ),
                    "source": "v3_device_traffic_peer_minutes" if traffic_ready else None,
                },
                "alert_capability": {
                    "available": incident_ready,
                    "reason": None if incident_ready else "incident_pipeline_not_ready",
                },
            }
        except sqlite3.Error as exc:
            raise MobileStoreUnavailable("mobile topology is unavailable") from exc
        finally:
            connection.close()

    @staticmethod
    def _protocol_category(protocol: str) -> tuple[str, str]:
        normalized = protocol.upper()
        if normalized == "TCP":
            return "tcp", "TCP 网络通信"
        if normalized == "UDP":
            return "udp", "UDP 网络通信"
        if normalized in {"ICMP", "ICMPV6", "ICMP6"}:
            return "network_diagnostics", "网络诊断通信"
        return "other", "其他或未分类"

    def traffic(
        self,
        principal: MobilePrincipal,
        device_id: str,
        window: str,
    ) -> dict:
        if window not in _WINDOWS:
            raise MobileDeviceTrafficWindowError(
                "只支持最近 15 分钟、1 小时或 24 小时"
            )
        duration, resolution_seconds = _WINDOWS[window]
        connection = self._connection()
        try:
            device = self._scoped_device(connection, principal, device_id)
            try:
                ensure_traffic_schema(connection)
            except TrafficStoreUnavailable as exc:
                raise MobileDeviceTrafficUnavailable(
                    "流量服务尚未准备好"
                ) from exc
            now = self._now()
            end = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            start = end - duration
            start_text, end_text = _iso(start), _iso(end)
            rows = connection.execute(
                "SELECT bucket_start,tx_bytes,rx_bytes,tx_packets,rx_packets,"
                "first_sample_at,last_sample_at FROM v3_device_traffic_minutes "
                "WHERE device_id=? AND bucket_start>=? AND bucket_start<? "
                "ORDER BY bucket_start",
                (device_id, start_text, end_text),
            ).fetchall()
            protocol_rows = connection.execute(
                "SELECT protocol,SUM(bytes) AS bytes,SUM(packets) AS packets "
                "FROM v3_device_traffic_protocol_minutes WHERE device_id=? "
                "AND bucket_start>=? AND bucket_start<? GROUP BY protocol",
                (device_id, start_text, end_text),
            ).fetchall()
            summary = None
            latest_sample: datetime | None = None
            trend_groups: dict[int, dict[str, int]] = {}
            if rows:
                summary = {
                    "uploaded_bytes": sum(int(item["tx_bytes"]) for item in rows),
                    "downloaded_bytes": sum(int(item["rx_bytes"]) for item in rows),
                    "uploaded_packets": sum(int(item["tx_packets"]) for item in rows),
                    "downloaded_packets": sum(int(item["rx_packets"]) for item in rows),
                }
                for item in rows:
                    bucket = _parse_iso(item["bucket_start"])
                    group_epoch = int(bucket.timestamp()) // resolution_seconds * resolution_seconds
                    point = trend_groups.setdefault(group_epoch, {
                        "uploaded_bytes": 0, "downloaded_bytes": 0,
                        "uploaded_packets": 0, "downloaded_packets": 0,
                    })
                    point["uploaded_bytes"] += int(item["tx_bytes"])
                    point["downloaded_bytes"] += int(item["rx_bytes"])
                    point["uploaded_packets"] += int(item["tx_packets"])
                    point["downloaded_packets"] += int(item["rx_packets"])
                    candidate = _parse_iso(item["last_sample_at"])
                    if latest_sample is None or candidate > latest_sample:
                        latest_sample = candidate
            categories: dict[str, dict[str, int | str]] = {}
            for item in protocol_rows:
                key, label = self._protocol_category(item["protocol"])
                value = categories.setdefault(key, {
                    "category": key, "label": label, "bytes": 0, "packets": 0,
                })
                value["bytes"] = int(value["bytes"]) + int(item["bytes"])
                value["packets"] = int(value["packets"]) + int(item["packets"])
            protocol_total = sum(int(item["bytes"]) for item in categories.values())
            protocols = [{
                **item,
                "share_percent": (
                    round(int(item["bytes"]) * 100 / protocol_total, 1)
                    if protocol_total else 0.0
                ),
            } for item in sorted(categories.values(), key=lambda value: str(value["category"]))]

            rate = self.traffic_service.realtime_window.snapshot(device_id)
            rate_available = rate.get("available") is True
            current_rate = {
                "status": "available" if rate_available else "warming_up",
                "label": "实时数据可用" if rate_available else "正在积累数据",
                "window_seconds": rate.get("window_seconds"),
                "as_of": rate.get("as_of"),
                "uploaded_bytes_per_second": rate.get("tx_bytes_per_second") if rate_available else None,
                "downloaded_bytes_per_second": rate.get("rx_bytes_per_second") if rate_available else None,
                "uploaded_packets_per_second": rate.get("tx_packets_per_second") if rate_available else None,
                "downloaded_packets_per_second": rate.get("rx_packets_per_second") if rate_available else None,
            }
            if latest_sample is None:
                freshness = {"status": "unavailable", "latest_sample_at": None}
                availability = {"status": "no_samples", "available": False, "reason": "no_samples"}
            else:
                age = max(0.0, (now - latest_sample).total_seconds())
                freshness = {
                    "status": "fresh" if age <= _STALE_SECONDS else "stale",
                    "latest_sample_at": _iso(latest_sample),
                }
                availability = {"status": "available", "available": True, "reason": None}
            retired = device["retired_at"] is not None
            return {
                "device_id": device_id,
                "window": window,
                "query_window": {"from": start_text, "to": end_text},
                "generated_at": _iso(now),
                "is_historical": retired,
                "availability": availability,
                "freshness": freshness,
                "current_rate": current_rate,
                "summary": summary,
                "trend_resolution_seconds": resolution_seconds,
                "trend": [{
                    "bucket_start": _bucket_iso(epoch), **trend_groups[epoch],
                } for epoch in sorted(trend_groups)],
                "protocols": protocols,
                "data_quality": {
                    "complete": None,
                    "message": "无法归属的样本不会计入本设备统计，统计可能不完整。",
                },
            }
        except sqlite3.Error as exc:
            raise MobileDeviceTrafficUnavailable(
                "流量服务暂时不可用"
            ) from exc
        finally:
            connection.close()


__all__ = [
    "MobileDeviceReadService",
    "MobileDeviceHistoryWindowError",
    "MobileDeviceUnavailable",
    "MobileDeviceTrafficUnavailable",
    "MobileDeviceTrafficWindowError",
    "MobileTopologyWindowError",
]
