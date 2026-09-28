"""设备图 GNN 检测接口（恢复边缘检测链路）。

提供设备级四级风险检测的 API：
  - /api/capture/start|stop|status  抓包控制
  - /api/device/status             设备 GNN 模型状态
  - /api/device/detect             触发设备级检测（4 级风险）
"""
from flask import Blueprint, jsonify, request

from services.auth import require_admin, require_operator
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
