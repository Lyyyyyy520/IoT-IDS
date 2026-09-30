"""
设备风险落库（边云协同共享入口）。

边缘端上报的「设备风险结果」与后端本地检测共用同一个落库逻辑：
  - 写 v3_device_current_state 的 risk 字段（DeviceStateService.update_device_risk）
  - 高危（橙/红）生成 v3 incident（按设备去重，避免每个窗口重复建事件）

风险等级语义：0=正常(绿) 1=侦察(黄) 2=DoS(橙) 3=僵尸网络(红)。
"""
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from services.device_state import DeviceStateService
from services.incident_workflow import IncidentActor
from v3_database import connect_v3_existing

LOGGER = logging.getLogger(__name__)

# level -> (severity, incident_type)
_RISK_INCIDENT = {
    2: ("high", "dos"),
    3: ("critical", "botnet"),
}
_OPEN_STATUSES = ("open", "acknowledged", "recovering")


def _has_open_incident(database_path: Path, device_id: str, incident_type: str) -> bool:
    """同一设备是否已有未关闭的同型 incident（用于去重）。"""
    connection = connect_v3_existing(database_path)
    try:
        row = connection.execute(
            "SELECT 1 FROM v3_incidents i "
            "JOIN v3_incident_devices d ON d.incident_id = i.incident_id "
            "WHERE d.device_id = ? AND d.incident_role = 'affected' "
            "AND i.incident_type = ? AND i.status IN (?, ?, ?) LIMIT 1",
            (device_id, incident_type, *_OPEN_STATUSES),
        ).fetchone()
        return row is not None
    finally:
        connection.close()


def ingest_device_risks(
    database_path,
    incident_service,
    risks,
    *,
    source_id: str,
    request_id: str | None = None,
) -> dict:
    """落库一批设备风险结果。risks: {ip: {"level", "name", "confidence"}}"""
    if not risks:
        return {"persisted": 0, "incidents_created": 0, "skipped": 0}

    path = Path(database_path)
    state = DeviceStateService(path, create_if_missing=False)
    occurred_at = datetime.now(timezone.utc)
    request_id = request_id or str(uuid4())

    persisted = 0
    incidents_created = 0
    skipped = 0

    for ip, r in risks.items():
        level = r.get("level") if isinstance(r, dict) else None
        if not isinstance(level, int) or not 0 <= level <= 3:
            skipped += 1
            continue

        name = str((r.get("name") or "")).strip() or f"风险等级{level}"
        confidence = r.get("confidence")
        if confidence is None and isinstance(r.get("probs"), (list, tuple)):
            probs = [float(p) for p in r["probs"] if isinstance(p, (int, float))]
            confidence = max(probs) if probs else None
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) \
                or not math.isfinite(confidence):
            confidence = 0.0
        confidence = min(max(float(confidence), 0.0), 1.0)

        device_id = state.device_id_for_ip(ip)
        if not device_id:
            skipped += 1
            continue

        state.update_device_risk(
            device_id=device_id,
            risk_level=level,
            risk_name=name,
            risk_confidence=confidence,
        )
        persisted += 1

        mapping = _RISK_INCIDENT.get(level)
        if not mapping or incident_service is None:
            continue
        severity, incident_type = mapping
        if _has_open_incident(path, device_id, incident_type):
            continue

        try:
            incident_service.create_incident(
                incident_type=incident_type,
                severity=severity,
                source="rule",
                admin_title=f"设备图检测：{name}",
                admin_summary=(
                    f"设备 {device_id}（{ip}）被判为 {name}，"
                    f"置信度 {confidence:.1%}"
                ),
                user_title="检测到设备安全风险",
                user_summary="系统发现一台设备出现异常网络行为，管理人员正在核实。",
                devices=[{
                    "device_id": device_id,
                    "incident_role": "affected",
                    "user_visible": True,
                }],
                publish_to_mobile=True,
                first_seen_at=occurred_at,
                public_progress="管理人员正在核实设备异常。",
                actor=IncidentActor(None, source_id, "system"),
                request_id=request_id,
            )
            incidents_created += 1
        except Exception as exc:
            LOGGER.error(
                "device_risk_incident_failed device=%s type=%s",
                device_id, type(exc).__name__,
            )

    return {
        "persisted": persisted,
        "incidents_created": incidents_created,
        "skipped": skipped,
    }
