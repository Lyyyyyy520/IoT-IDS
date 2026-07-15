"""
IoT IDS Backend — Flask API Server v2.0
"""
from flask import Flask, jsonify, session, request
from flask_cors import CORS
from datetime import datetime
import os

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()  # Session encryption key
app.config['SESSION_PERMANENT'] = True
CORS(app, supports_credentials=True)

# Init database on startup
from database import init_db, query_all, query_one, execute
init_db()

# Import auth services
from services.auth import require_auth, require_admin, login_user, logout_user, get_current_user, log_action

# Register probe blueprint
from api.probe import probe_bp
app.register_blueprint(probe_bp)

# ---- Health Check ----
_MODEL_PATH = os.path.join(os.path.dirname(__file__), 'data', 'best_model.onnx')
_model_loaded = os.path.exists(_MODEL_PATH)

@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'model_loaded': _model_loaded,
        'model_path': _MODEL_PATH if _model_loaded else None,
        'uptime': 0,
        'timestamp': datetime.now().isoformat(),
    })

# ---- Authentication Routes ----
@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'success': False, 'message': '请输入账号和密码'}), 400
    result = login_user(username, password)
    if result['success']:
        return jsonify(result)
    return jsonify(result), 401

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    result = logout_user()
    return jsonify(result)

@app.route('/api/auth/me')
def auth_me():
    user = get_current_user()
    if not user:
        return jsonify({'authenticated': False}), 401
    return jsonify({'authenticated': True, 'user': user})


# ---- Dashboard Stats ----
@app.route('/api/dashboard/stats')
def dashboard_stats():
    # Count from real DB tables
    total_alerts = query_one("SELECT COUNT(*) as c FROM alerts")['c']
    alerts_today = query_one(
        "SELECT COUNT(*) as c FROM alerts WHERE date(created_at) = date('now', 'localtime')"
    )['c']
    active_threats = query_one(
        "SELECT COUNT(DISTINCT src_ip) as c FROM alerts WHERE status = 'new' AND risk_level IN ('critical','high')"
    )['c']
    total_assets = query_one("SELECT COUNT(*) as c FROM assets")['c']
    online_assets = query_one("SELECT COUNT(*) as c FROM assets WHERE status = 'online'")['c']
    total_traffic = query_one("SELECT COUNT(*) as c FROM traffic_logs")['c']

    # Attack distribution
    dist_rows = query_all(
        "SELECT attack_type, COUNT(*) as count FROM alerts GROUP BY attack_type ORDER BY count DESC LIMIT 6"
    )
    attack_distribution = [{'type': r['attack_type'], 'count': r['count']} for r in dist_rows]

    # Recent 5 alerts
    recent = query_all(
        "SELECT id, risk_level, attack_type, src_ip, dst_ip, confidence, created_at as timestamp, merged_count, status, description FROM alerts ORDER BY created_at DESC LIMIT 5"
    )

    # Risk score: count critical/high alerts vs total
    critical_count = query_one("SELECT COUNT(*) as c FROM alerts WHERE risk_level = 'critical' AND status = 'new'")['c']
    high_count = query_one("SELECT COUNT(*) as c FROM alerts WHERE risk_level = 'high' AND status = 'new'")['c']
    risk_score = max(5, 100 - (critical_count * 15 + high_count * 8))
    risk_score = min(100, risk_score)

    return jsonify({
        'total_scanned': total_traffic,
        'alerts_today': alerts_today,
        'total_alerts': total_alerts,
        'active_threats': active_threats,
        'total_assets': total_assets,
        'online_assets': online_assets,
        'risk_score': risk_score,
        'system_status': 'normal' if risk_score > 60 else 'warning',
        'traffic_history': [
            {'time': '14:00', 'normal': 1200, 'attack': 45},
            {'time': '14:05', 'normal': 1180, 'attack': 32},
            {'time': '14:10', 'normal': 1350, 'attack': 28},
            {'time': '14:15', 'normal': 1420, 'attack': 55},
            {'time': '14:20', 'normal': 1280, 'attack': 38},
            {'time': '14:25', 'normal': 1390, 'attack': 23},
            {'time': '14:30', 'normal': 1450, 'attack': 42},
        ],
        'attack_distribution': attack_distribution,
        'recent_alerts': recent,
    })


# ---- Alerts List ----
@app.route('/api/alerts')
def alerts_list():
    from flask import request
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', 20, type=int)
    risk_filter = request.args.get('risk_level', '')
    type_filter = request.args.get('attack_type', '')

    where = []
    params = []
    if risk_filter and risk_filter != 'all':
        where.append("risk_level = ?")
        params.append(risk_filter)
    if type_filter and type_filter != 'all':
        where.append("attack_type = ?")
        params.append(type_filter)

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    order_clause = """
        ORDER BY
            CASE risk_level WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
            created_at DESC
    """

    count_sql = f"SELECT COUNT(*) as c FROM alerts {where_clause}"
    total = query_one(count_sql, params)['c']

    offset = (page - 1) * page_size
    items = query_all(
        f"SELECT id, risk_level, attack_type, src_ip, dst_ip, confidence, src_port, dst_port, protocol, created_at as timestamp, merged_count, status, description "
        f"FROM alerts {where_clause} {order_clause} LIMIT ? OFFSET ?",
        params + [page_size, offset],
    )

    return jsonify({
        'total': total,
        'page': page,
        'page_size': page_size,
        'items': items,
    })


# ---- Analysis Data ----
@app.route('/api/analysis/topology')
def topology_data():
    return jsonify({
        'nodes': [
            {'id': 'router', 'label': '社区路由器', 'type': 'router', 'risk': 'normal'},
            {'id': 'cam1', 'label': '摄像头-01', 'type': 'camera', 'risk': 'normal'},
            {'id': 'cam2', 'label': '摄像头-02', 'type': 'camera', 'risk': 'critical'},
            {'id': 'door1', 'label': '门禁-01', 'type': 'door', 'risk': 'normal'},
            {'id': 'door2', 'label': '门禁-02', 'type': 'door', 'risk': 'high'},
            {'id': 'sensor1', 'label': '烟感-01', 'type': 'sensor', 'risk': 'normal'},
            {'id': 'sensor2', 'label': '温湿度-01', 'type': 'sensor', 'risk': 'normal'},
            {'id': 'socket1', 'label': '智能插座-01', 'type': 'socket', 'risk': 'medium'},
            {'id': 'lock1', 'label': '智能锁-01', 'type': 'lock', 'risk': 'normal'},
            {'id': 'hub1', 'label': '智能网关', 'type': 'hub', 'risk': 'normal'},
            {'id': 'phone1', 'label': '业主手机', 'type': 'phone', 'risk': 'normal'},
            {'id': 'server', 'label': '管理服务器', 'type': 'server', 'risk': 'normal'},
        ],
        'links': [
            {'source': 'router', 'target': 'cam1'}, {'source': 'router', 'target': 'cam2'},
            {'source': 'router', 'target': 'door1'}, {'source': 'router', 'target': 'door2'},
            {'source': 'router', 'target': 'hub1'},
            {'source': 'hub1', 'target': 'sensor1'}, {'source': 'hub1', 'target': 'sensor2'},
            {'source': 'hub1', 'target': 'socket1'}, {'source': 'hub1', 'target': 'lock1'},
            {'source': 'router', 'target': 'server'}, {'source': 'router', 'target': 'phone1'},
        ],
    })


@app.route('/api/analysis/heatmap')
def heatmap_data():
    import random
    random.seed(42)
    data = []
    hours = list(range(0, 24, 2))
    for d in range(7):
        for h_idx, h in enumerate(hours):
            val = random.randint(5, 30)
            if h < 4 or h > 20:
                val += random.randint(10, 40)
            if d >= 5:
                val += random.randint(10, 20)
            data.append({'day': d, 'hour': h_idx, 'value': min(val, 100)})
    return jsonify({'data': data, 'days': ['周一','周二','周三','周四','周五','周六','周日'], 'hours': [f'{h:02d}' for h in hours]})


@app.route('/api/analysis/mitre')
def mitre_data():
    return jsonify({
        'stages': [
            {'name': '初始侦查', 'mitre': 'Recon', 'desc': '端口扫描探测', 'active': True},
            {'name': '武器构建', 'mitre': 'ResourceDev', 'desc': '恶意载荷生成', 'active': True},
            {'name': '交付投递', 'mitre': 'InitAccess', 'desc': '漏洞利用投递', 'active': True},
            {'name': '漏洞利用', 'mitre': 'Execution', 'desc': '远程代码执行', 'active': True},
            {'name': 'C2通信', 'mitre': 'C2', 'desc': 'C2服务器通信', 'active': True},
            {'name': '数据窃取', 'mitre': 'Exfil', 'desc': '敏感数据外传', 'active': False},
        ],
        'links': [
            {'source': 'Recon', 'target': 'ResourceDev', 'value': 25},
            {'source': 'ResourceDev', 'target': 'InitAccess', 'value': 22},
            {'source': 'InitAccess', 'target': 'Execution', 'value': 18},
            {'source': 'Execution', 'target': 'C2', 'value': 15},
            {'source': 'C2', 'target': 'Exfil', 'value': 8},
        ],
    })


# ---- Detection ----
@app.route('/api/detect/upload', methods=['POST'])
def detect_upload():
    from flask import request
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    filename = file.filename or 'unknown'

    # Save uploaded file
    import os, tempfile
    tmp_dir = tempfile.mkdtemp()
    filepath = os.path.join(tmp_dir, filename)
    file.save(filepath)

    # Run detection
    from services.feature_extract import FeatureExtractor
    from models.inference import get_engine

    extractor = FeatureExtractor()
    features_list = extractor.extract_from_pcap(filepath)
    engine = get_engine()

    results = []
    for feat in features_list:
        result = engine.predict(feat)
        results.append({
            'class_name': result['class_name'],
            'confidence': result['confidence'],
            'risk_level': result['risk_level'],
            'is_attack': result['is_attack'],
        })

    # Cleanup
    try:
        os.remove(filepath)
        os.rmdir(tmp_dir)
    except Exception:
        pass

    # Summarize
    attack_count = sum(1 for r in results if r['is_attack'])
    return jsonify({
        'job_id': os.urandom(8).hex(),
        'filename': filename,
        'total_samples': len(results),
        'attack_count': attack_count,
        'normal_count': len(results) - attack_count,
        'results': results,
    })


# ---- Export ----
@app.route('/api/export/excel')
def export_excel():
    from flask import Response
    from openpyxl import Workbook
    from io import BytesIO

    wb = Workbook()
    ws = wb.active
    ws.title = '告警记录'

    # Header
    headers = ['ID', '风险等级', '攻击类型', '源IP', '目标IP', '置信度', '重复次数', '时间', '状态', '描述']
    ws.append(headers)

    # Data (same mock data as /api/alerts)
    alerts = [
        [1, '高危', 'Mirai', '192.168.1.105', '192.168.1.1', 0.97, 5, '2026-07-14 14:30:22', '新', 'Mirai 僵尸网络扫描'],
        [2, '高危', 'Mirai', '192.168.1.200', '192.168.1.1', 0.95, 8, '2026-07-14 14:20:47', '新', 'Mirai 暴力破解 Telnet'],
        [3, '中危', 'Gafgyt', '10.0.0.23', '10.0.0.1', 0.89, 3, '2026-07-14 14:28:15', '新', 'Gafgyt DDoS 攻击'],
        [4, '中危', 'Gafgyt', '10.0.0.45', '10.0.0.100', 0.87, 1, '2026-07-14 13:55:10', '已阅', 'Gafgyt UDP Flood'],
        [5, '低危', 'Hajime', '172.16.0.8', '172.16.0.1', 0.76, 1, '2026-07-14 14:25:01', '新', 'Hajime P2P 传播'],
    ]
    for row in alerts:
        ws.append(row)

    # Style header
    from openpyxl.styles import Font, PatternFill
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill(start_color='2B5797', end_color='2B5797', fill_type='solid')

    # Set column widths
    widths = [5, 8, 10, 18, 18, 8, 8, 20, 8, 30]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else 'A'].width = w

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename=iot_ids_alerts.xlsx'},
    )


# ---- Traffic Capture Control ----
from services.traffic_capture import get_capture

@app.route('/api/capture/start', methods=['POST'])
def capture_start():
    data = request.get_json() or {}
    use_scapy = data.get('use_scapy', False)
    result = get_capture().start(use_scapy=use_scapy)
    log_action('capture_start', f'mode={result["mode"]}')
    return jsonify(result)


@app.route('/api/capture/stop', methods=['POST'])
def capture_stop():
    result = get_capture().stop()
    log_action('capture_stop', f'packets={result["packet_count"]}')
    return jsonify(result)


@app.route('/api/capture/status')
def capture_status():
    return jsonify(get_capture().status())


# ---- Traffic Logs ----
@app.route('/api/traffic/logs')
def traffic_logs():
    rows = query_all(
        "SELECT id, timestamp, src_ip, dst_ip, src_port, dst_port, protocol, length, flags "
        "FROM traffic_logs ORDER BY timestamp DESC LIMIT 50"
    )
    return jsonify({'items': rows})


# ---- Alert Actions ----
@app.route('/api/alerts/<int:alert_id>/block', methods=['POST'])
def block_ip(alert_id):
    return jsonify({'success': True, 'message': f'IP blocked for alert #{alert_id}'})


@app.route('/api/alerts/<int:alert_id>/trace', methods=['POST'])
def trace_alert(alert_id):
    return jsonify({
        'success': True,
        'trace_info': f'溯源完成：攻击来源位于社区网络3号区域，关联设备 192.168.1.105，历史关联告警 5 条'
    })


@app.route('/api/alerts/<int:alert_id>/false-positive', methods=['POST'])
def mark_false_positive(alert_id):
    return jsonify({'success': True, 'message': f'Alert #{alert_id} marked as false positive'})


# ---- Configuration ----
@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify({
        'detection_mode': 'offline',
        'confidence_threshold': 0.85,
        'merge_window_minutes': 5,
        'auto_block': False,
        'model_name': 'CNN-LSTM-Light',
        'model_version': 'v1.0',
        'input_features': 21,
        'output_classes': 5,
    })


if __name__ == '__main__':
    print('IoT IDS Backend starting...')
    print('    http://localhost:5000/api/health')
    app.run(host='0.0.0.0', port=5000, debug=True)
