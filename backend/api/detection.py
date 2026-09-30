"""设备图 GNN 检测接口（边云协同）。

提供设备级四级风险检测的 API：
  - /api/capture/start|stop|status  抓包控制（后端本地模式）
  - /api/device/status             设备 GNN 模型状态
  - /api/device/detect             后端本地触发设备级检测（4 级风险）
  - /api/device/risk               接收边缘端上报的设备风险结果（边云协同）
"""
from flask import Blueprint, current_app, jsonify, request

from api.probe import require_probe_auth
from runtime_services import get_service_container
from services.auth import require_admin, require_operator
from services.device_risk_ingest import ingest_device_risks
from services.traffic_capture import get_capture

detection_bp = Blueprint("detection", __name__)


@detection_bp.route("/api/capture/start", methods=["POST"])
@require_admin
def capture_start():
    data = request.get_json() or {}
    use_scapy = data.get("use_scapy", False)
    attack_ratio = data.get("attack_ratio", 0.25)
    interface = data.get("interface")
    result = get_capture().start(
        interface=interface, use_scapy=use_scapy, attack_ratio=attack_ratio
    )
    return jsonify(result)


@detection_bp.route("/api/capture/stop", methods=["POST"])
@require_admin
def capture_stop():
    return jsonify(get_capture().stop())


@detection_bp.route("/api/capture/status")
@require_operator
def capture_status():
    return jsonify(get_capture().status())


@detection_bp.route("/api/device/status")
@require_operator
def device_status():
    status = get_capture().status()
    return jsonify({
        "model_loaded": status.get("device_gnn_loaded", False),
        "available": status.get("device_gnn_available", False),
        "model": "Device Graph GNN (HTGAT)",
        "classification": "4-level device risk (正常/侦察/拒绝服务/僵尸网络)",
        "feature_dim": 13,
        "dataset": "CICIoT2023",
    })


@detection_bp.route("/api/device/detect", methods=["POST"])
@require_admin
def device_detect():
    result = get_capture().device_detect()
    return jsonify({"success": True, "devices": result, "count": len(result)})


@detection_bp.route("/api/device/risk", methods=["POST"])
@require_probe_auth
def device_risk():
    """接收边缘端上报的设备风险结果：写 v3 风险字段 + 高危转 v3 incident。"""
    data = request.get_json(silent=True) or {}
    devices = data.get("devices")
    if not isinstance(devices, list) or not devices:
        return jsonify({"success": False, "message": "缺少 devices"}), 400

    risks = {}
    for item in devices:
        if not isinstance(item, dict):
            continue
        ip = item.get("ip")
        if not isinstance(ip, str) or not ip.strip():
            continue
        risks[ip.strip()] = {
            "level": item.get("level"),
            "name": item.get("name"),
            "confidence": item.get("confidence"),
        }
    if not risks:
        return jsonify({"success": False, "message": "无有效设备风险"}), 400

    container = get_service_container(current_app._get_current_object())
    database_path = container.database_path
    if database_path is None:
        return jsonify({"success": False, "message": "数据库未配置"}), 503

    incident_service = current_app.extensions.get("iot_ids_incident_workflow")
    result = ingest_device_risks(
        database_path,
        incident_service,
        risks,
        source_id=str(data.get("source_id") or "edge-detection"),
    )
    return jsonify({"success": True, **result})
