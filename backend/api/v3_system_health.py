"""Admin/operator read-only operational health API."""
from datetime import datetime, timezone
from typing import Callable

from flask import Blueprint, current_app, jsonify, request, session

from contracts import Role
from services.auth import effective_role
from services.system_health import build_system_health


def create_v3_system_health_blueprint(
    database_path,
    service_container,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Blueprint:
    """Register a read-only health snapshot without probing external services."""
    blueprint = Blueprint("v3_system_health", __name__)

    @blueprint.get("/api/v3/system/health")
    def system_health():
        authorization = request.headers.get("Authorization", "").strip().lower()
        if authorization.startswith("bearer "):
            return jsonify({"error": {"code": "web_session_required"}}), 403
        if "user_id" not in session:
            return jsonify({"error": {"code": "unauthenticated"}}), 401
        role = effective_role(session.get("username", ""), session.get("role"))
        session["role"] = role
        if role not in {Role.ADMIN.value, Role.OPERATOR.value}:
            return jsonify({"error": {"code": "forbidden"}}), 403

        try:
            now = clock() if clock is not None else datetime.now(timezone.utc)
            payload = build_system_health(
                database_path, service_container, now=now
            )
        except Exception:
            # Health reporting itself remains redacted and does not turn a
            # diagnostic failure into an exception trace sent to the caller.
            observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            payload = {
                "observed_at": observed_at,
                "components": {
                    "api": {
                        "status": "ready",
                        "updated_at": observed_at,
                        "reason_code": None,
                    },
                    "database": {
                        "status": "degraded",
                        "updated_at": observed_at,
                        "reason_code": "database_open_failed",
                        "exists": None,
                        "readable": False,
                        "writable": None,
                    },
                    "schema": {
                        "status": "unavailable",
                        "updated_at": observed_at,
                        "reason_code": "schema_invalid",
                        "version": 0,
                        "migration_complete": False,
                        "migration_checksums_valid": None,
                    },
                    "integrity_check": {
                        "status": "unavailable",
                        "updated_at": observed_at,
                        "reason_code": "database_open_failed",
                        "result": "unavailable",
                        "checked_at": None,
                    },
                    "mqtt": {
                        "status": "unavailable",
                        "updated_at": observed_at,
                        "reason_code": "mqtt_not_started",
                    },
                    "traffic": {
                        "status": "unavailable",
                        "updated_at": observed_at,
                        "reason_code": "schema_incomplete",
                        "aggregation_status": "unavailable",
                    },
                    "event_log": {
                        "status": "unavailable",
                        "updated_at": observed_at,
                        "reason_code": "event_log_unavailable",
                    },
                    "incident": {
                        "status": "unavailable",
                        "updated_at": observed_at,
                        "reason_code": "schema_incomplete",
                    },
                    "mobile": {
                        "status": "unavailable",
                        "updated_at": observed_at,
                        "reason_code": "schema_incomplete",
                    },
                    "discovery": {
                        "status": "unavailable",
                        "updated_at": observed_at,
                        "reason_code": "schema_incomplete",
                    },
                    "graph": {
                        "status": "unavailable",
                        "updated_at": observed_at,
                        "reason_code": "graph_capability_unavailable",
                    },
                },
                "maintenance": {
                    "last_successful_at": None,
                    "last_plan_at": None,
                    "last_apply_at": None,
                    "last_plan_reason_code": "maintenance_plan_read_only",
                    "reason_code": "no_maintenance_run",
                },
                "capacity": {
                    "database_file_bytes": None,
                    "page_size_bytes": None,
                    "page_count": None,
                    "free_pages": None,
                    "free_bytes": None,
                    "disk_free_bytes": None,
                },
                "automatic_maintenance": False,
            }
        response = jsonify(payload)
        response.headers["Cache-Control"] = "no-store"
        return response

    return blueprint


__all__ = ["create_v3_system_health_blueprint"]
