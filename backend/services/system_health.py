"""Read-only, redacted operational health snapshot for the v3 API."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Callable

from runtime_services import _sqlite_failure_reason, inspect_database


Clock = Callable[[], datetime]
_ALLOWED_REASONS = {
    "database_path_not_configured",
    "database_file_missing",
    "database_open_failed",
    "database_busy",
    "database_locked",
    "database_read_only",
    "database_disk_full",
    "database_corrupt",
    "database_io_error",
    "database_wal_mode_unsupported",
    "database_unavailable",
    "schema_ledger_missing",
    "schema_incomplete",
    "schema_invalid",
    "schema_checksum_mismatch",
    "schema_object_missing",
    "schema_version_unsupported",
    "legacy_schema_unavailable",
    "integrity_check_failed",
    "mqtt_disabled",
    "mqtt_not_started",
    "mqtt_start_failed",
    "mqtt_configuration_error",
    "mqtt_managed_by_single_worker",
    "debug_reloader_parent",
    "traffic_not_started",
    "worker_stop_timeout",
    "traffic_store_unavailable",
    "aggregation_transaction_failed",
    "candidate_capacity_reached",
    "discovery_storage_error",
    "component_restarting",
    "maintenance_plan_read_only",
    "event_log_unavailable",
    "event_log_gap_requires_snapshot",
    "event_log_requires_snapshot",
    "sse_replay_window_pruned",
    "graph_capability_unavailable",
    "no_maintenance_run",
    "request_failed",
    "component_degraded",
}


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        return _iso(datetime.fromisoformat(normalized))
    except (TypeError, ValueError):
        return None


def _safe_reason(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text in _ALLOWED_REASONS else "component_degraded"


def _component(
    status: str,
    reason: str | None,
    updated_at: str,
    **details: Any,
) -> dict:
    if status not in {"ready", "warming_up", "degraded", "unavailable"}:
        status = "degraded"
        reason = "component_degraded"
    return {
        "status": status,
        "updated_at": updated_at,
        "reason_code": _safe_reason(reason),
        **details,
    }


def _database_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro", uri=True, timeout=0.0
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _with_runtime_override(container, name: str, component: dict) -> dict:
    with container._lock:
        reason = container.degraded_reasons.get(name)
        if reason is not None and component["status"] == "ready":
            container.degraded_reasons.pop(name, None)
            reason = None
    if reason is None:
        return component
    return _component("degraded", _safe_reason(reason), component["updated_at"], **{
        key: value for key, value in component.items()
        if key not in {"status", "updated_at", "reason_code"}
    })


def build_system_health(
    database_path: str | Path | None,
    container,
    *,
    now: datetime | None = None,
) -> dict:
    """Inspect readiness, schema, log continuity, and capacity without writes."""
    observed_at = _iso(now or datetime.now(timezone.utc))
    database = inspect_database(database_path)
    configured_path = (
        Path(database_path).expanduser().resolve()
        if database_path is not None and str(database_path).strip()
        else None
    )
    try:
        database_exists = bool(configured_path and configured_path.is_file())
    except OSError:
        database_exists = False
    database_details = {
        "exists": database_exists,
        "readable": bool(database["available"]),
        "writable": database.get("writable"),
    }
    if database["available"] and database.get("writable") is False:
        database_component = _component(
            "degraded", "database_read_only", observed_at, **database_details
        )
    elif database["available"]:
        database_component = _component(
            "ready", None, observed_at, **database_details
        )
    elif database["reason"] in {"database_path_not_configured", "database_file_missing"}:
        database_component = _component(
            "unavailable", database["reason"], observed_at, **database_details
        )
    else:
        database_component = _component(
            "degraded", database["reason"], observed_at, **database_details
        )
    database_component = _with_runtime_override(container, "database", database_component)

    if database["available"] and database["v3_schema_ready"]:
        schema_status = "ready" if database["legacy_schema_ready"] else "degraded"
        schema_reason = None if database["legacy_schema_ready"] else "legacy_schema_unavailable"
    elif database["available"]:
        schema_status = "degraded"
        schema_reason = database["reason"] or "schema_incomplete"
    else:
        schema_status = "unavailable"
        schema_reason = database["reason"] or "database_unavailable"
    components = {
        "api": _component("ready", None, observed_at),
        "database": database_component,
        "schema": _component(
            schema_status, schema_reason, observed_at,
            version=database["schema_version"],
            legacy_schema_ready=database["legacy_schema_ready"],
            migration_complete=bool(database["v3_schema_ready"]),
            migration_checksums_valid=(
                None if not database["available"] else
                database["reason"] not in {"schema_checksum_mismatch", "schema_invalid"}
            ),
        ),
        "integrity_check": _component(
            "unavailable", database["reason"] or "database_unavailable",
            observed_at, result="unavailable", checked_at=None,
        ),
        "mqtt": _component("unavailable", "mqtt_disabled", observed_at),
        "traffic": _component(
            "unavailable", database["reason"] or "schema_incomplete", observed_at,
        ),
        "event_log": _component("unavailable", "event_log_unavailable", observed_at),
        "incident": _component(
            "unavailable", database["reason"] or "schema_incomplete", observed_at,
        ),
        "mobile": _component(
            "unavailable", database["reason"] or "schema_incomplete", observed_at,
        ),
        "discovery": _component(
            "unavailable", database["reason"] or "schema_incomplete", observed_at,
        ),
        "graph": _component("unavailable", "graph_capability_unavailable", observed_at),
    }
    maintenance = {
        "last_successful_at": None,
        "last_plan_at": None,
        "last_apply_at": None,
        "last_plan_reason_code": "maintenance_plan_read_only",
        "reason_code": "no_maintenance_run",
    }
    capacity = {
        "database_file_bytes": None,
        "page_size_bytes": None,
        "page_count": None,
        "free_pages": None,
        "free_bytes": None,
        "disk_free_bytes": None,
    }

    if configured_path is not None and database["available"]:
        connection = None
        try:
            connection = _database_connection(configured_path)
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            integrity_ok = bool(integrity_rows) and all(row[0] == "ok" for row in integrity_rows)
            components["integrity_check"] = _component(
                "ready" if integrity_ok else "degraded",
                None if integrity_ok else "integrity_check_failed",
                observed_at, result="ok" if integrity_ok else "failed",
                checked_at=observed_at,
            )
            if not integrity_ok:
                components["database"] = _component(
                    "degraded", "integrity_check_failed", observed_at,
                )
            elif database.get("writable") is not False:
                container.clear_degraded("database", "api")

            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            free_pages = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
            capacity.update({
                "page_size_bytes": page_size,
                "page_count": page_count,
                "free_pages": free_pages,
                "free_bytes": free_pages * page_size,
            })

            if database["v3_schema_ready"]:
                try:
                    event_row = connection.execute(
                        "SELECT COUNT(*),MIN(event_id),MAX(event_id) FROM v3_realtime_events"
                    ).fetchone()
                    sequence_row = connection.execute(
                        "SELECT seq FROM sqlite_sequence WHERE name='v3_realtime_events'"
                    ).fetchone()
                    event_count = int(event_row[0])
                    first_id = int(event_row[1]) if event_row[1] is not None else None
                    last_id = int(event_row[2]) if event_row[2] is not None else None
                    sequence = int(sequence_row[0]) if sequence_row else 0
                    event_status = "ready"
                    event_reason = None
                    if not integrity_ok:
                        event_status, event_reason = "degraded", "integrity_check_failed"
                    elif event_count == 0 and sequence > 0:
                        event_status, event_reason = "degraded", "event_log_requires_snapshot"
                    elif event_count and last_id - first_id + 1 != event_count:
                        event_status, event_reason = "degraded", "event_log_gap_requires_snapshot"
                    elif event_count and first_id > 1:
                        event_reason = "sse_replay_window_pruned"
                    components["event_log"] = _component(
                        event_status, event_reason, observed_at,
                        retained_events=event_count,
                        oldest_event_id=first_id,
                        latest_event_id=last_id,
                        latest_cursor=sequence,
                    )
                    maintenance_row = connection.execute(
                        "SELECT updated_at FROM v3_system_component_health "
                        "WHERE component_id='database_maintenance'"
                    ).fetchone()
                    if maintenance_row is not None:
                        last_apply = _safe_timestamp(maintenance_row[0])
                        maintenance = {
                            "last_successful_at": last_apply,
                            "last_plan_at": None,
                            "last_apply_at": last_apply,
                            "last_plan_reason_code": "maintenance_plan_read_only",
                            "reason_code": None if last_apply else "component_degraded",
                        }
                    traffic_row = connection.execute(
                        "SELECT readiness,reason,updated_at FROM v3_system_component_health "
                        "WHERE component_id='traffic-aggregation'"
                    ).fetchone()
                    if not integrity_ok:
                        traffic_status, traffic_reason = "degraded", "integrity_check_failed"
                    elif traffic_row is None:
                        traffic_status, traffic_reason = "warming_up", "traffic_not_started"
                    else:
                        traffic_status = str(traffic_row[0])
                        traffic_reason = _safe_reason(traffic_row[1])
                        updated = _safe_timestamp(traffic_row[2])
                        runtime_started = getattr(container, "runtime_started_at", None)
                        if (
                            traffic_status == "ready" and updated and runtime_started
                            and datetime.fromisoformat(updated.replace("Z", "+00:00")) < runtime_started
                        ):
                            traffic_status, traffic_reason = "warming_up", "component_restarting"
                    if traffic_status not in {"ready", "warming_up", "degraded"}:
                        traffic_status, traffic_reason = "degraded", "component_degraded"
                    components["traffic"] = _component(
                        traffic_status, traffic_reason, observed_at,
                        aggregation_status=traffic_status,
                    )
                    components["incident"] = _component("ready", None, observed_at)
                    components["mobile"] = _component("ready", None, observed_at)
                    components["discovery"] = _component("ready", None, observed_at)
                except sqlite3.Error as exc:
                    reason = _safe_reason(_sqlite_failure_reason(exc)) or "event_log_unavailable"
                    components["event_log"] = _component("degraded", reason, observed_at)
                    for name in ("traffic", "incident", "mobile", "discovery"):
                        components[name] = _component("degraded", reason, observed_at)
            else:
                components["event_log"] = _component(
                    "unavailable", "event_log_unavailable", observed_at,
                )
                for name in ("traffic", "incident", "mobile", "discovery"):
                    components[name] = _component(
                        "unavailable", database["reason"] or "schema_incomplete", observed_at,
                    )
            connection.close()
            connection = None

            try:
                stat = configured_path.stat()
                capacity["database_file_bytes"] = int(stat.st_size)
                capacity["disk_free_bytes"] = int(shutil.disk_usage(configured_path.parent).free)
            except OSError:
                pass
        except sqlite3.Error as exc:
            reason = _safe_reason(_sqlite_failure_reason(exc)) or "database_open_failed"
            components["database"] = _component("degraded", reason, observed_at)
            components["integrity_check"] = _component(
                "degraded", reason, observed_at, result="unavailable",
                checked_at=observed_at,
            )
            if database["v3_schema_ready"]:
                components["event_log"] = _component("degraded", reason, observed_at)
        finally:
            if connection is not None:
                connection.close()

    mqtt_state = container.mqtt_state
    if mqtt_state == "running":
        components["mqtt"] = _component("ready", None, observed_at)
    elif mqtt_state == "failed":
        reason = container.mqtt_reason or "mqtt_start_failed"
        mapped = reason if reason in _ALLOWED_REASONS else "mqtt_start_failed"
        components["mqtt"] = _component("degraded", mapped, observed_at)
    elif mqtt_state == "disabled":
        components["mqtt"] = _component("unavailable", "mqtt_disabled", observed_at)
    elif mqtt_state == "skipped":
        reason = container.mqtt_reason or "mqtt_not_started"
        mapped = reason if reason in _ALLOWED_REASONS else "mqtt_not_started"
        status = "warming_up" if mapped == "debug_reloader_parent" else "unavailable"
        components["mqtt"] = _component(status, mapped, observed_at)
    else:
        try:
            settings = container.mqtt_settings_provider()
            if bool(getattr(settings, "enabled", False)):
                components["mqtt"] = _component("warming_up", "mqtt_not_started", observed_at)
            else:
                components["mqtt"] = _component("unavailable", "mqtt_disabled", observed_at)
        except Exception:
            components["mqtt"] = _component("degraded", "mqtt_configuration_error", observed_at)
    components["graph"] = _component(
        "unavailable", "graph_capability_unavailable", observed_at,
    )
    for name in ("traffic", "event_log", "incident", "mobile", "discovery", "mqtt", "api"):
        components[name] = _with_runtime_override(container, name, components[name])

    return {
        "observed_at": observed_at,
        "components": components,
        "maintenance": maintenance,
        "capacity": capacity,
        "automatic_maintenance": False,
    }


__all__ = ["build_system_health"]
