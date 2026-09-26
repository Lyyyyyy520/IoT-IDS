"""Authenticated edge probe endpoints retained for v3 ingestion and control."""
from datetime import datetime, timezone
import hmac
import ipaddress
import logging
import math
import re
from functools import wraps
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request, session

from config import probe_token
from contracts import Role
from database import execute, query_one
from services.auth import effective_role, require_admin
from services.device_traffic import resolve_ip_binding
from services.incident_workflow import IncidentActor
from runtime_services import get_service_container
from v3_database import connect_v3_existing

probe_bp = Blueprint("probe", __name__)
LOGGER = logging.getLogger(__name__)
_SAMPLE_FIELDS = {
    "sample_id", "occurred_at", "src_ip", "dst_ip", "network_protocol",
    "application_protocol", "application_protocol_inferred", "src_port",
    "dst_port", "bytes", "packets", "flow_count",
}
_SEVERITIES = {"info", "low", "medium", "high", "critical"}


def _ingest_versioned_traffic(data: dict) -> dict:
    """Send only contract fields to the shared v3 aggregation service."""
    version = data.get("schema_version")
    if version is None:
        return {"status": "not_ingested", "reason_code": "legacy_probe_schema_no_idempotency"}
    if type(version) is not int or version != 2:
        return {"status": "rejected", "reason_code": "unsupported_schema_version"}
    required = {"source_id", "source_session_id", "batch_id", "batch_sequence", "flows"}
    if not required <= set(data) or not isinstance(data.get("flows"), list):
        return {"status": "rejected", "reason_code": "invalid_batch_envelope"}
    samples = [
        {key: value for key, value in flow.items() if key in _SAMPLE_FIELDS}
        if isinstance(flow, dict) else flow
        for flow in data["flows"]
    ]
    service = get_service_container(current_app._get_current_object()).get_traffic_service()
    try:
        return service.ingest_batch(
            source_id=data["source_id"],
            source_session_id=data["source_session_id"],
            batch_id=data["batch_id"],
            batch_sequence=data["batch_sequence"],
            samples=samples,
            received_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        reason = getattr(exc, "code", "aggregation_failed")
        service.mark_degraded(reason)
        LOGGER.error("probe_traffic_ingest_failed code=%s type=%s", reason, type(exc).__name__)
        return {"status": "degraded", "reason_code": reason}


def _request_probe_token() -> str:
    explicit = request.headers.get("X-Probe-Token", "").strip()
    if explicit:
        return explicit
    authorization = request.headers.get("Authorization", "")
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""


def require_probe_auth(f):
    """Require the independent probe credential on every probe-side route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        expected = probe_token()
        if not expected:
            return jsonify({"error": "探针凭据未配置"}), 503
        supplied = _request_probe_token()
        if not supplied:
            return jsonify({"error": "缺少探针凭据"}), 401
        if not hmac.compare_digest(supplied, expected):
            return jsonify({"error": "探针凭据无效"}), 403
        return f(*args, **kwargs)
    return decorated


def require_probe_or_admin(f):
    """Allow a probe credential or an authenticated admin browser session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        expected = probe_token()
        supplied = _request_probe_token()
        if expected and supplied and hmac.compare_digest(supplied, expected):
            return f(*args, **kwargs)
        role = effective_role(session.get("username", ""), session.get("role"))
        if session.get("user_id") and role == Role.ADMIN.value:
            return f(*args, **kwargs)
        if not expected:
            return jsonify({"error": "探针凭据未配置"}), 503
        return jsonify({"error": "缺少或无效的探针凭据"}), 401
    return decorated


def _validated_ip(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return ipaddress.ip_address(value.strip()).compressed
    except ValueError:
        return False


def _create_probe_incident(alert, *, database_path, incident_service, observed_at):
    if not isinstance(alert, dict):
        return {"status": "rejected", "reason_code": "invalid_alert"}
    severity = alert.get("risk_level", "medium")
    if not isinstance(severity, str) or severity.lower() not in _SEVERITIES:
        return {"status": "rejected", "reason_code": "invalid_alert_severity"}
    severity = severity.lower()
    attack_type = alert.get("attack_type", "probe-detection")
    if not isinstance(attack_type, str):
        return {"status": "rejected", "reason_code": "invalid_alert_type"}
    incident_type = re.sub(r"[^a-z0-9_.-]+", "-", attack_type.strip().lower()).strip("-.")[:64]
    if not incident_type:
        incident_type = "probe-detection"
    source_ip = _validated_ip(alert.get("src_ip", ""))
    target_ip = _validated_ip(alert.get("dst_ip", ""))
    if source_ip is False or target_ip is False or target_ip is None:
        return {"status": "rejected", "reason_code": "invalid_alert_address"}

    source_port = alert.get("src_port", 0)
    target_port = alert.get("dst_port", 0)
    for value in (source_port, target_port):
        if type(value) is not int or value < 0 or value > 65535:
            return {"status": "rejected", "reason_code": "invalid_alert_port"}
    protocol = alert.get("protocol", "")
    if not isinstance(protocol, str) or len(protocol) > 16:
        return {"status": "rejected", "reason_code": "invalid_alert_protocol"}
    protocol = re.sub(r"[^A-Za-z0-9_-]", "", protocol).upper() or "UNKNOWN"
    confidence = alert.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or confidence < 0
        or confidence > 1
    ):
        return {"status": "rejected", "reason_code": "invalid_alert_confidence"}

    try:
        connection = connect_v3_existing(database_path)
        try:
            target_status, target_device = resolve_ip_binding(connection, target_ip, observed_at)
            source_device = None
            if source_ip is not None:
                source_status, source_device = resolve_ip_binding(connection, source_ip, observed_at)
                if source_status != "resolved":
                    source_device = None
            if target_status != "resolved" or target_device is None:
                return {"status": "unassigned", "reason_code": target_status}
        finally:
            connection.close()

        devices = [{
            "device_id": target_device,
            "incident_role": "affected",
            "user_visible": True,
        }]
        if source_device and source_device != target_device:
            devices.append({
                "device_id": source_device,
                "incident_role": "suspected_source",
                "user_visible": False,
            })
        evidence = (
            f"Probe {incident_type}; {source_ip or 'unknown'}:{source_port} -> "
            f"{target_ip}:{target_port}; protocol={protocol}"
        )
        if confidence is not None:
            evidence += f"; confidence={float(confidence):.4f}"
        incident = incident_service.create_incident(
            incident_type=incident_type,
            severity=severity,
            source="rule",
            admin_title=f"探针规则初判：{incident_type}",
            admin_summary=evidence,
            user_title="检测到可疑网络活动",
            user_summary="系统发现与一台已登记设备相关的异常通信，管理人员正在核实。",
            devices=devices,
            publish_to_mobile=True,
            first_seen_at=observed_at,
            public_progress="管理人员正在核实探针上报的异常通信。",
            actor=IncidentActor(None, "probe-ingestion", "system"),
            request_id=str(uuid4()),
        )
        return {"status": "accepted", "incident_id": incident["incident_id"]}
    except Exception as exc:
        reason = getattr(exc, "code", "incident_store_unavailable")
        LOGGER.error("probe_incident_ingest_failed code=%s type=%s", reason, type(exc).__name__)
        return {"status": "degraded", "reason_code": reason}


@probe_bp.post("/api/probe/register")
@require_probe_auth
def register():
    """Register a legacy edge-probe identity used by the control loop."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "Unknown Probe")
    ip = request.remote_addr or "unknown"
    existing = query_one("SELECT id FROM assets WHERE ip_address = ? AND device_type = 'probe'", (ip,))
    if existing:
        execute("UPDATE assets SET status='online', last_seen=datetime('now','localtime') WHERE id=?", (existing["id"],))
        return jsonify({"success": True, "probe_id": existing["id"], "message": "Probe re-registered"})
    probe_id = execute(
        "INSERT INTO assets (name, ip_address, device_type, status, last_seen) "
        "VALUES (?,?,?,?,datetime('now','localtime'))",
        (str(name)[:128], ip, "probe", "online"),
    )
    return jsonify({"success": True, "probe_id": probe_id, "message": "Probe registered"})


@probe_bp.post("/api/probe/heartbeat")
@require_probe_auth
def heartbeat():
    """Record edge-probe liveness in its legacy compatibility row."""
    data = request.get_json(silent=True) or {}
    probe_id = data.get("probe_id")
    ip = request.remote_addr or "unknown"
    if type(probe_id) is int and probe_id > 0:
        execute("UPDATE assets SET status='online', last_seen=datetime('now','localtime') WHERE id=?", (probe_id,))
    else:
        execute("UPDATE assets SET status='online', last_seen=datetime('now','localtime') WHERE ip_address=? AND device_type='probe'", (ip,))
    return jsonify({"success": True, "timestamp": datetime.now(timezone.utc).isoformat()})


@probe_bp.post("/api/probe/push")
@require_probe_auth
def push_data():
    """Receive idempotent probe v2 traffic and forward located alerts to v3."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return jsonify({"success": False, "message": "No data provided"}), 400
    alerts = data.get("alerts", [])
    if not isinstance(alerts, list) or len(alerts) > 100:
        return jsonify({"success": False, "message": "Invalid alert batch"}), 400

    probe_ip = request.remote_addr or "unknown"
    execute(
        "UPDATE assets SET status='online', last_seen=datetime('now','localtime') "
        "WHERE ip_address=? AND device_type='probe'",
        (probe_ip,),
    )
    traffic = _ingest_versioned_traffic(data)
    incident_results = []
    accepted_batch = (
        traffic.get("status") in {"committed", "partial"}
        and int(traffic.get("accepted_samples", 0)) > 0
    )
    if alerts and accepted_batch:
        container = get_service_container(current_app._get_current_object())
        incident_service = current_app.extensions.get("iot_ids_incident_workflow")
        if incident_service is None:
            incident_results = [{"status": "degraded", "reason_code": "incident_store_unavailable"}]
        else:
            observed_at = datetime.now(timezone.utc)
            incident_results = [
                _create_probe_incident(
                    alert,
                    database_path=container.database_path,
                    incident_service=incident_service,
                    observed_at=observed_at,
                )
                for alert in alerts
            ]
    elif alerts:
        reason = "duplicate_probe_batch" if traffic.get("status") == "duplicate" else "alerts_require_accepted_v2_batch"
        incident_results = [{"status": "not_ingested", "reason_code": reason}]

    return jsonify({
        "success": traffic.get("status") not in {"rejected", "degraded"},
        "flows_received": traffic.get("accepted_samples", 0),
        "alerts_received": sum(result.get("status") == "accepted" for result in incident_results),
        "alerts_unassigned": sum(result.get("status") == "unassigned" for result in incident_results),
        "alerts_rejected": sum(result.get("status") == "rejected" for result in incident_results),
        "alert_ingestion": incident_results,
        "traffic_aggregation": traffic,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }), (200 if traffic.get("status") not in {"rejected", "degraded"} else 400)


@probe_bp.post("/api/probe/control")
@require_admin
def probe_control():
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    probe_name = data.get("probe_name", "")
    if not isinstance(action, str) or not isinstance(probe_name, str) or not action or not probe_name:
        return jsonify({"success": False}), 400
    execute("INSERT OR REPLACE INTO config (key, value) VALUES (?,?)", (f"probe_control_{probe_name[:128]}", action[:32]))
    return jsonify({"success": True, "action": action[:32]})


@probe_bp.get("/api/probe/control-status")
@require_probe_or_admin
def probe_control_status():
    name = request.args.get("name", "Pi-001")[:128]
    row = query_one("SELECT value FROM config WHERE key = ?", (f"probe_control_{name}",))
    status_row = query_one("SELECT value FROM config WHERE key = ?", (f"probe_status_{name}",))
    return jsonify({
        "action": row["value"] if row else "stop",
        "capturing": status_row["value"] == "running" if status_row else False,
    })


@probe_bp.post("/api/probe/status-report")
@require_probe_auth
def probe_status_report():
    """Record the edge-probe state used by its explicit control loop."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "")
    status = data.get("status", "stopped")
    if not isinstance(name, str) or not isinstance(status, str) or status not in {"running", "stopped"}:
        return jsonify({"success": False}), 400
    if name:
        execute("INSERT OR REPLACE INTO config (key, value) VALUES (?,?)", (f"probe_status_{name[:128]}", status))
    return jsonify({"success": True})
