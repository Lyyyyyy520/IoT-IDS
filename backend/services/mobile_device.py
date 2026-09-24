"""Strictly scoped mobile device summaries and simplified traffic views."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


_WINDOWS = {
    "15m": (timedelta(minutes=15), 60),
    "1h": (timedelta(hours=1), 300),
    "24h": (timedelta(hours=24), 3600),
}
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
    "MobileDeviceUnavailable",
    "MobileDeviceTrafficUnavailable",
    "MobileDeviceTrafficWindowError",
]
