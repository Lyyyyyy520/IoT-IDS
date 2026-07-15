"""
Probe Integration API — receives data from Raspberry Pi probe nodes
"""
from flask import Blueprint, request, jsonify
from database import query_all, query_one, execute
from datetime import datetime

probe_bp = Blueprint('probe', __name__)


@probe_bp.route('/api/probe/register', methods=['POST'])
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

    # Process alerts
    for alert in data.get('alerts', []):
        execute(
            "INSERT INTO alerts (risk_level, attack_type, src_ip, dst_ip, src_port, dst_port, protocol, confidence, description, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,'new')",
            (
                alert.get('risk_level', 'medium'),
                alert.get('attack_type', 'Other'),
                alert.get('src_ip', ''),
                alert.get('dst_ip', ''),
                alert.get('src_port', 0),
                alert.get('dst_port', 0),
                alert.get('protocol', ''),
                alert.get('confidence', 0.8),
                f"[Probe:{probe_name}] {alert.get('description', '')}",
            ),
        )
        alerts_received += 1

    # Process flows
    for flow in data.get('flows', []):
        execute(
            "INSERT INTO traffic_logs (src_ip, dst_ip, src_port, dst_port, protocol, length, flags) VALUES (?,?,?,?,?,?,?)",
            (
                flow.get('src_ip', ''),
                flow.get('dst_ip', ''),
                flow.get('src_port', 0),
                flow.get('dst_port', 0),
                flow.get('protocol', ''),
                flow.get('length', 0),
                flow.get('flags', ''),
            ),
        )
        flows_received += 1

    return jsonify({
        'success': True,
        'alerts_received': alerts_received,
        'flows_received': flows_received,
        'timestamp': datetime.now().isoformat(),
    })


@probe_bp.route('/api/probe/list')
def list_probes():
    """List all registered probes."""
    probes = query_all("SELECT * FROM assets WHERE device_type = 'probe' ORDER BY last_seen DESC")
    return jsonify({'probes': probes})


@probe_bp.route('/api/probe/status')
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
