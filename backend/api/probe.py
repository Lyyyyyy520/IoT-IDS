"""
Probe Integration API — receives data from Raspberry Pi probe nodes
"""
from functools import wraps
import hmac
import logging

from flask import Blueprint, current_app, request, jsonify, session
from database import query_all, query_one, execute
from datetime import datetime, timezone
from config import probe_token
from contracts import Role
from services.auth import effective_role, require_admin, require_operator

probe_bp = Blueprint('probe', __name__)
LOGGER = logging.getLogger(__name__)


def _ingest_versioned_traffic(data: dict) -> dict:
    """Adapt the probe v2 envelope to the shared strict aggregation service."""
    version = data.get('schema_version')
    if version is None:
        return {
            'status': 'not_ingested',
            'reason_code': 'legacy_probe_schema_no_idempotency',
        }
    if version != 2:
        return {'status': 'rejected', 'reason_code': 'unsupported_schema_version'}
    required = {
        'source_id', 'source_session_id', 'batch_id', 'batch_sequence', 'flows'
    }
    if not required <= set(data):
        return {'status': 'rejected', 'reason_code': 'invalid_batch_envelope'}
    if not isinstance(data.get('flows'), list):
        return {'status': 'rejected', 'reason_code': 'invalid_batch_envelope'}
    sample_fields = {
        'sample_id', 'occurred_at', 'src_ip', 'dst_ip', 'network_protocol',
        'application_protocol', 'application_protocol_inferred', 'src_port',
        'dst_port', 'bytes', 'packets', 'flow_count',
    }
    samples = [
        {key: value for key, value in flow.items() if key in sample_fields}
        if isinstance(flow, dict) else flow
        for flow in data['flows']
    ]
    from runtime_services import get_service_container

    service = get_service_container(
        current_app._get_current_object()
    ).get_traffic_service()
    try:
        return service.ingest_batch(
            source_id=data['source_id'],
            source_session_id=data['source_session_id'],
            batch_id=data['batch_id'],
            batch_sequence=data['batch_sequence'],
            samples=samples,
            received_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        reason = getattr(exc, 'code', 'aggregation_failed')
        service.mark_degraded(reason)
        LOGGER.error(
            'probe_traffic_ingest_failed code=%s type=%s',
            reason, type(exc).__name__,
        )
        return {'status': 'degraded', 'reason_code': reason}


def _request_probe_token() -> str:
    explicit = request.headers.get('X-Probe-Token', '').strip()
    if explicit:
        return explicit
    authorization = request.headers.get('Authorization', '')
    if authorization.lower().startswith('bearer '):
        return authorization[7:].strip()
    return ''


def require_probe_auth(f):
    """Require the independent probe credential on every probe-side route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        expected = probe_token()
        if not expected:
            return jsonify({'error': '探针凭据未配置'}), 503
        supplied = _request_probe_token()
        if not supplied:
            return jsonify({'error': '缺少探针凭据'}), 401
        if not hmac.compare_digest(supplied, expected):
            return jsonify({'error': '探针凭据无效'}), 403
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
        role = effective_role(session.get('username', ''), session.get('role'))
        if session.get('user_id') and role == Role.ADMIN.value:
            return f(*args, **kwargs)
        if not expected:
            return jsonify({'error': '探针凭据未配置'}), 503
        return jsonify({'error': '缺少或无效的探针凭据'}), 401
    return decorated


@probe_bp.route('/api/probe/register', methods=['POST'])
@require_probe_auth
def register():
    """Register a new probe node."""
    data = request.get_json() or {}
    name = data.get('name', 'Unknown Probe')
    ip = request.remote_addr or 'unknown'

    existing = query_one("SELECT id FROM assets WHERE ip_address = ? AND device_type = 'probe'", (ip,))
    if existing:
        execute("UPDATE assets SET status='online', last_seen=datetime('now','localtime') WHERE id=?", (existing['id'],))
        return jsonify({'success': True, 'probe_id': existing['id'], 'message': 'Probe re-registered'})

    probe_id = execute(
        "INSERT INTO assets (name, ip_address, device_type, status, last_seen) VALUES (?,?,?,?,datetime('now','localtime'))",
        (name, ip, 'probe', 'online'),
    )
    return jsonify({'success': True, 'probe_id': probe_id, 'message': 'Probe registered'})


@probe_bp.route('/api/probe/heartbeat', methods=['POST'])
@require_probe_auth
def heartbeat():
    """Probe heartbeat — updates last_seen timestamp."""
    data = request.get_json() or {}
    probe_id = data.get('probe_id')
    ip = request.remote_addr or 'unknown'

    if probe_id:
        execute("UPDATE assets SET status='online', last_seen=datetime('now','localtime') WHERE id=?", (probe_id,))
    else:
        execute("UPDATE assets SET status='online', last_seen=datetime('now','localtime') WHERE ip_address=? AND device_type='probe'", (ip,))

    return jsonify({'success': True, 'timestamp': datetime.now().isoformat()})


@probe_bp.route('/api/probe/push', methods=['POST'])
@require_probe_auth
def push_data():
    """
    Receive alert/flow data from a probe.
    Expected JSON format:
    {
        "probe_id": 1,
        "probe_name": "Pi-LivingRoom",
        "alerts": [
            {
                "risk_level": "critical",
                "attack_type": "Mirai",
                "src_ip": "192.168.1.105",
                "dst_ip": "192.168.1.1",
                "src_port": 54321,
                "dst_port": 23,
                "protocol": "TCP",
                "confidence": 0.95,
                "description": "Mirai SYN scan detected by Suricata"
            }
        ],
        "flows": [...]
    }
    """
    data = request.get_json() or {}
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400

    probe_name = data.get('probe_name', 'Unknown')
    probe_ip = request.remote_addr or 'unknown'

    # Update probe online status
    execute(
        "UPDATE assets SET status='online', last_seen=datetime('now','localtime') WHERE ip_address=? AND device_type='probe'",
        (probe_ip,),
    )

    alerts_received = 0
    flows_received = 0
    traffic_aggregation = _ingest_versioned_traffic(data)

    # Process alerts
    for alert in data.get('alerts', []):
        # 匹配目标设备
        dst_ip = alert.get('dst_ip', '')
        device_name = None
        if dst_ip:
            asset_row = query_one("SELECT id, name FROM assets WHERE ip_address = ? AND device_type != 'probe'", (dst_ip,))
            if asset_row:
                device_name = asset_row['name']
                # 更新设备风险等级
                execute("UPDATE assets SET risk_level = ?, status = 'alert', last_seen = datetime('now','localtime') WHERE id = ?",
                        (alert.get('risk_level', 'high'), asset_row['id']))

        desc = f"[Probe:{probe_name}] {alert.get('description', '')}"
        if device_name:
            desc = f"[Probe:{probe_name}] 目标: {device_name}({dst_ip}) - {alert.get('description', '')}"

        execute(
            "INSERT INTO alerts (risk_level, attack_type, src_ip, dst_ip, src_port, dst_port, protocol, confidence, description, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,'new')",
            (
                alert.get('risk_level', 'medium'),
                alert.get('attack_type', 'Other'),
                alert.get('src_ip', ''),
                dst_ip,
                alert.get('src_port', 0),
                alert.get('dst_port', 0),
                alert.get('protocol', ''),
                alert.get('confidence', 0.8),
                desc,
            ),
        )
        alerts_received += 1

    # Process flows through the same IDS pipeline as local capture so remote
    # probe traffic is evaluated by rules and models before it is stored.
    from services.traffic_capture import get_capture
    capture = get_capture()
    for flow in data.get('flows', []):
        try:
            capture.process_external_packet(
                src_ip=flow.get('src_ip', ''),
                dst_ip=flow.get('dst_ip', ''),
                src_port=int(flow.get('src_port', 0) or 0),
                dst_port=int(flow.get('dst_port', 0) or 0),
                protocol=flow.get('network_protocol', flow.get('protocol', '')),
                length=int(flow.get('bytes', flow.get('length', 0)) or 0),
                flags=flow.get('flags', ''),
                payload=flow.get('payload', ''),
                source=flow.get('source', 'real'),
                aggregate_v3=False,
            )
            flows_received += 1
        except Exception as exc:
            LOGGER.error(
                'probe_legacy_flow_failed code=legacy_pipeline_failure type=%s',
                type(exc).__name__,
            )

    return jsonify({
        'success': True,
        'alerts_received': alerts_received,
        'flows_received': flows_received,
        'traffic_aggregation': traffic_aggregation,
        'timestamp': datetime.now().isoformat(),
    })


@probe_bp.route('/api/probe/list')
@require_operator
def list_probes():
    """List all registered probes."""
    probes = query_all("SELECT * FROM assets WHERE device_type = 'probe' ORDER BY last_seen DESC")
    return jsonify({'probes': probes})


@probe_bp.route('/api/probe/status')
@require_operator
def probe_status():
    """Get aggregate probe status."""
    total = query_one("SELECT COUNT(*) as c FROM assets WHERE device_type='probe'")['c']
    online = query_one("SELECT COUNT(*) as c FROM assets WHERE device_type='probe' AND status='online'")['c']
    offline = total - online
    alerts_from_probes = query_one("SELECT COUNT(*) as c FROM alerts WHERE description LIKE '[Probe:%'")['c']

    return jsonify({
        'total_probes': total,
        'online_probes': online,
        'offline_probes': offline,
        'alerts_from_probes': alerts_from_probes,
    })


@probe_bp.route('/api/probe/control', methods=['POST'])
@require_admin
def probe_control():
    data = request.get_json() or {}
    action = data.get('action', '')
    probe_name = data.get('probe_name', '')
    if not action or not probe_name:
        return jsonify({'success': False}), 400
    from database import execute
    execute("INSERT OR REPLACE INTO config (key, value) VALUES (?,?)",
            (f'probe_control_{probe_name}', action))
    return jsonify({'success': True, 'action': action})


@probe_bp.route('/api/probe/control-status', methods=['GET'])
@require_probe_or_admin
def probe_control_status():
    from database import query_one, execute
    name = request.args.get('name', 'Pi-001')
    row = query_one("SELECT value FROM config WHERE key = ?", (f'probe_control_{name}',))
    status_row = query_one("SELECT value FROM config WHERE key = ?", (f'probe_status_{name}',))
    return jsonify({
        'action': row['value'] if row else 'stop',
        'capturing': status_row['value'] == 'running' if status_row else False
    })


@probe_bp.route('/api/probe/status-report', methods=['POST'])
@require_probe_auth
def probe_status_report():
    """探头主动上报运行状态"""
    from database import execute
    data = request.get_json() or {}
    name = data.get('name', '')
    status = data.get('status', 'stopped')  # running / stopped
    if name:
        execute("INSERT OR REPLACE INTO config (key, value) VALUES (?,?)",
                (f'probe_status_{name}', status))
    return jsonify({'success': True})
