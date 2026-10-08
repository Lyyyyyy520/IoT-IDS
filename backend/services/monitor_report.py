"""Bounded CSV export for real monitor status and retained traffic trends."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import sqlite3
from typing import Callable

from services.device_traffic import TrafficStoreUnavailable, ensure_traffic_schema
from services.realtime_events import RealtimeEventStore, V3DatabaseUnavailable


Clock = Callable[[], datetime]
RANGES = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}
CONNECTION_STATUSES = {"unknown", "online", "stale", "offline"}
RISK_FILTERS = {"all", "undetected", "normal", "alerted"}
MAX_DEVICES = 10_000
MAX_TRAFFIC_ROWS = 20_000
DEVICE_QUERY_BATCH = 400


class MonitorReportInputError(ValueError):
    """A report filter is outside the documented contract."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise V3DatabaseUnavailable("monitor report clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _csv_safe(value):
    text = "" if value is None else str(value)
    if text and text[0] in "=+-@\t\r":
        return "'" + text
    return text


def _chunks(values: list[str], size: int):
    for index in range(0, len(values), size):
        yield values[index:index + size]


class MonitorReportService:
    def __init__(
        self,
        database_path: str | Path | None,
        *,
        clock: Clock | None = None,
    ):
        self.database_path = Path(database_path) if database_path else None
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._events = RealtimeEventStore(self.database_path)

    @staticmethod
    def _filter_devices(rows, *, search, connection_status, device_type, risk_filter):
        query = search.casefold() if search else ""
        selected = []
        for row in rows:
            if connection_status and row["connection_status"] != connection_status:
                continue
            if device_type and row["device_type"] != device_type:
                continue
            risk = row["risk_level"]
            if risk_filter == "undetected" and risk is not None:
                continue
            if risk_filter == "normal" and risk != 0:
                continue
            if risk_filter == "alerted" and (risk is None or risk == 0):
                continue
            if query:
                haystack = " ".join(
                    str(row[key] or "")
                    for key in ("device_id", "display_name", "device_type", "area_id", "ip_address")
                ).casefold()
                if query not in haystack:
                    continue
            selected.append(row)
        return selected

    def export_csv(
        self,
        *,
        search: str | None = None,
        connection_status: str | None = None,
        device_type: str | None = None,
        risk_filter: str = "all",
        range_key: str = "24h",
    ) -> str:
        if search is not None and len(search) > 160:
            raise MonitorReportInputError("search must be at most 160 characters")
        if connection_status is not None and connection_status not in CONNECTION_STATUSES:
            raise MonitorReportInputError("connection_status is invalid")
        if device_type is not None and (not device_type or len(device_type) > 80):
            raise MonitorReportInputError("device_type is invalid")
        if risk_filter not in RISK_FILTERS:
            raise MonitorReportInputError("risk_filter is invalid")
        if range_key not in RANGES:
            raise MonitorReportInputError("range must be 15m, 1h, 6h, 24h, or 7d")

        generated_at = _utc(self._clock())
        start = generated_at - RANGES[range_key]
        start_text, end_text = _iso(start), _iso(generated_at)
        resolution = "hour" if range_key == "7d" else "minute"

        try:
            with self._events.connection() as connection:
                ensure_traffic_schema(connection)
                connection.execute("BEGIN")
                rows = connection.execute(
                    "SELECT p.device_id,p.display_name,p.device_type,p.area_id,"
                    "p.operation_mode,s.connection_status,s.ip_address,s.risk_level,"
                    "s.risk_name,s.risk_confidence,s.risk_detected_at "
                    "FROM v3_device_profiles p "
                    "JOIN v3_device_current_state s ON s.device_id=p.device_id "
                    "ORDER BY p.device_id LIMIT ?",
                    (MAX_DEVICES + 1,),
                ).fetchall()
                device_limit_reached = len(rows) > MAX_DEVICES
                devices = self._filter_devices(
                    rows[:MAX_DEVICES],
                    search=search,
                    connection_status=connection_status,
                    device_type=device_type,
                    risk_filter=risk_filter,
                )
                device_ids = [row["device_id"] for row in devices]
                buckets_by_device: dict[str, list[sqlite3.Row]] = {}
                sampled_device_ids: set[str] = set()
                traffic_limit_reached = False

                bucket_expression = (
                    "substr(bucket_start,1,13) || ':00:00Z'"
                    if resolution == "hour" else "bucket_start"
                )
                for batch in _chunks(device_ids, DEVICE_QUERY_BATCH):
                    placeholders = ",".join("?" for _ in batch)
                    sampled = connection.execute(
                        "SELECT DISTINCT device_id FROM v3_device_traffic_minutes "
                        "WHERE device_id IN (" + placeholders + ") "
                        "AND bucket_start>=? AND bucket_start<?",
                        (*batch, start_text, end_text),
                    ).fetchall()
                    sampled_device_ids.update(row["device_id"] for row in sampled)

                    remaining = MAX_TRAFFIC_ROWS - sum(
                        len(values) for values in buckets_by_device.values()
                    )
                    if remaining <= 0:
                        traffic_limit_reached = True
                        break
                    result = connection.execute(
                        "SELECT device_id," + bucket_expression + " AS bucket_start,"
                        "SUM(tx_bytes) AS tx_bytes,SUM(rx_bytes) AS rx_bytes,"
                        "SUM(tx_packets) AS tx_packets,SUM(rx_packets) AS rx_packets,"
                        "SUM(tx_flow_count) AS tx_flows,SUM(rx_flow_count) AS rx_flows "
                        "FROM v3_device_traffic_minutes WHERE device_id IN (" + placeholders + ") "
                        "AND bucket_start>=? AND bucket_start<? "
                        "GROUP BY device_id," + bucket_expression + " "
                        "ORDER BY device_id,bucket_start LIMIT ?",
                        (*batch, start_text, end_text, remaining + 1),
                    ).fetchall()
                    if len(result) > remaining:
                        result = result[:remaining]
                        traffic_limit_reached = True
                    for point in result:
                        buckets_by_device.setdefault(point["device_id"], []).append(point)
                    if traffic_limit_reached:
                        break

                connection.commit()
        except (FileNotFoundError, sqlite3.Error, TrafficStoreUnavailable) as exc:
            raise V3DatabaseUnavailable("monitor report database is unavailable") from exc

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["# 数据时间范围（UTC，左闭右开）: " + start_text + " 至 " + end_text])
        writer.writerow(["# 趋势粒度: " + ("按小时汇总" if resolution == "hour" else "按分钟")])
        writer.writerow(["# 生成时间（UTC）: " + end_text])
        if device_limit_reached or traffic_limit_reached:
            writer.writerow([
                "# 结果已截断：设备最多导出 10000 台、趋势桶最多导出 20000 条；请缩小筛选范围后重新导出。"
            ])
        writer.writerow([
            "record_type", "device_id", "display_name", "device_type", "area_id",
            "connection_status", "operation_mode", "ip_address", "risk_level",
            "risk_name", "risk_confidence", "risk_detected_at", "range_from",
            "range_to", "bucket_start", "tx_bytes", "rx_bytes", "tx_packets",
            "rx_packets", "tx_flows", "rx_flows", "traffic_data_status",
            "traffic_resolution",
        ])
        for device in devices:
            base = [
                _csv_safe(device["device_id"]), _csv_safe(device["display_name"]),
                _csv_safe(device["device_type"]), _csv_safe(device["area_id"]),
                _csv_safe(device["connection_status"]), _csv_safe(device["operation_mode"]),
                _csv_safe(device["ip_address"]), device["risk_level"],
                _csv_safe(device["risk_name"]), device["risk_confidence"],
                _csv_safe(device["risk_detected_at"]), start_text, end_text,
            ]
            has_samples = device["device_id"] in sampled_device_ids
            writer.writerow([
                "device", *base, "", "", "", "", "", "", "",
                "available" if has_samples else "no_samples", resolution,
            ])
            for point in buckets_by_device.get(device["device_id"], []):
                writer.writerow([
                    "traffic_bucket", *base, _csv_safe(point["bucket_start"]),
                    int(point["tx_bytes"]), int(point["rx_bytes"]),
                    int(point["tx_packets"]), int(point["rx_packets"]),
                    int(point["tx_flows"]), int(point["rx_flows"]),
                    "recorded", resolution,
                ])
        return output.getvalue()


__all__ = ["MonitorReportInputError", "MonitorReportService"]
