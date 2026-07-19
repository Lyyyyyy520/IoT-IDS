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
    merged = request.args.get('merged', 'false').lower() == 'true'

    # 筛选条件
    where_parts = []
    where_params = []
    if risk_filter and risk_filter != 'all':
        where_parts.append("risk_level = ?")
        where_params.append(risk_filter)
    if type_filter and type_filter != 'all':
        where_parts.append("attack_type = ?")
        where_params.append(type_filter)
    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    order_clause = """
        ORDER BY
            CASE risk_level WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
            created_at DESC
    """

    if merged:
        # 合并模式：按 attack_type + src_ip + dst_ip 分组
        merge_sql = f"""
            SELECT
                MIN(a.id) as id,
                CASE
                    WHEN SUM(CASE WHEN a.risk_level = 'critical' THEN 1 ELSE 0 END) > 0 THEN 'critical'
                    WHEN SUM(CASE WHEN a.risk_level = 'high' THEN 1 ELSE 0 END) > 0 THEN 'high'
                    WHEN SUM(CASE WHEN a.risk_level = 'medium' THEN 1 ELSE 0 END) > 0 THEN 'medium'
                    ELSE 'low'
                END as risk_level,
                a.attack_type,
                a.src_ip,
                a.dst_ip,
                MAX(a.confidence) as confidence,
                MIN(a.created_at) as timestamp,
                COUNT(*) as merged_count,
                CASE
                    WHEN SUM(CASE WHEN a.status = 'blocked' THEN 1 ELSE 0 END) > 0 THEN 'blocked'
                    WHEN SUM(CASE WHEN a.status = 'false_positive' THEN 1 ELSE 0 END) > 0 THEN 'false_positive'
                    WHEN SUM(CASE WHEN a.status = 'new' THEN 1 ELSE 0 END) > 0 THEN 'new'
                    WHEN SUM(CASE WHEN a.status = 'reviewed' THEN 1 ELSE 0 END) > 0 THEN 'reviewed'
                    WHEN SUM(CASE WHEN a.status = 'resolved' THEN 1 ELSE 0 END) > 0 THEN 'resolved'
                    ELSE 'new'
                END as status,
                (SELECT a2.description FROM alerts a2
                 WHERE a2.attack_type = a.attack_type AND a2.src_ip = a.src_ip AND a2.dst_ip = a.dst_ip
                 ORDER BY a2.created_at DESC LIMIT 1) as description
            FROM alerts a
            {where_clause}
            GROUP BY a.attack_type, a.src_ip, a.dst_ip
        """
        merge_order = """
            ORDER BY
                CASE risk_level WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                timestamp DESC
        """
        count_sql = f"SELECT COUNT(*) as c FROM ({merge_sql})"
        total = query_one(count_sql, where_params)['c']

        offset = (page - 1) * page_size
        items_sql = f"SELECT * FROM ({merge_sql}) {merge_order} LIMIT ? OFFSET ?"
        items = query_all(items_sql, where_params + [page_size, offset])
    else:
        # 不合并模式：原始逐条显示
        count_sql = f"SELECT COUNT(*) as c FROM alerts {where_clause}"
        total = query_one(count_sql, where_params)['c']

        offset = (page - 1) * page_size
        items = query_all(
            f"SELECT id, risk_level, attack_type, src_ip, dst_ip, confidence, src_port, dst_port, protocol, "
            f"created_at as timestamp, 1 as merged_count, status, description "
            f"FROM alerts {where_clause} {order_clause} LIMIT ? OFFSET ?",
            where_params + [page_size, offset],
        )

    return jsonify({
        'total': total,
        'page': page,
        'page_size': page_size,
        'items': items,
    })


@app.route('/api/alerts/new')
def alerts_new():
    """查询新告警，用于前端实时轮询"""
    since_id = request.args.get('since_id', 0, type=int)

    items = query_all(
        "SELECT id, risk_level, attack_type, src_ip, dst_ip, confidence, "
        "created_at as timestamp, status, description "
        "FROM alerts WHERE id > ? "
        "ORDER BY id DESC LIMIT 20",
        (since_id,),
    )

    max_id_row = query_one("SELECT MAX(id) as max_id FROM alerts")
    max_id = max_id_row['max_id'] if max_id_row else 0

    return jsonify({'items': items, 'max_id': max_id})


# ---- Analysis Data ----
@app.route('/api/analysis/topology')
def topology_data():
    # 从 assets 表查所有设备作为节点
    assets = query_all("SELECT id, name, device_type, status, ip_address FROM assets")
    nodes = []
    for row in assets:
        risk_map = {
            'online': 'normal',
            'offline': 'high',
            'alert': 'critical',
        }
        nodes.append({
            'id': str(row['id']),
            'label': row['name'],
            'type': row['device_type'] if row['device_type'] else 'unknown',
            'risk': risk_map.get(row['status'], 'normal'),
            'ip': row['ip_address'],
        })

    # 从 alerts 表推导设备之间的连接关系
    links = []
    try:
        links_sql = """
            SELECT DISTINCT
                a1.id as source_id,
                a2.id as target_id
            FROM alerts al
            JOIN assets a1 ON al.src_ip = a1.ip_address
            JOIN assets a2 ON al.dst_ip = a2.ip_address
            WHERE a1.id != a2.id
            LIMIT 100
        """
        conns = query_all(links_sql)
        links = [{'source': str(row['source_id']), 'target': str(row['target_id'])} for row in conns]
    except Exception:
        pass

    return jsonify({
        'nodes': nodes,
        'links': links,
    })


@app.route('/api/analysis/heatmap')
def heatmap_data():
    # 统计最近 7 天、24 小时的告警分布
    sql = """
        SELECT
            strftime('%w', created_at) as dow,
            strftime('%H', created_at) as hour,
            COUNT(*) as count
        FROM alerts
        WHERE created_at >= datetime('now', '-7 days')
        GROUP BY dow, hour
        ORDER BY dow, hour
    """
    rows = query_all(sql)

    data = []
    for row in rows:
        data.append([int(row['hour']), int(row['dow']), row['count']])

    days = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
    hours = [f'{h:02d}:00' for h in range(24)]

    return jsonify({
        'data': data,
        'days': days,
        'hours': hours,
    })


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
    """拉黑 IP：写入 policies 黑名单 + 更新告警状态 + 记录审计日志"""
    alert = query_one(
        "SELECT id, src_ip, attack_type, dst_ip, description FROM alerts WHERE id = ?",
        (alert_id,),
    )
    if not alert:
        return jsonify({'success': False, 'message': '告警不存在'}), 404

    src_ip = alert['src_ip']
    attack_type = alert['attack_type']

    # 检查是否已在黑名单中
    existing = query_one(
        "SELECT id FROM policies WHERE policy_type = 'blacklist' AND target = ?",
        (src_ip,),
    )
    if not existing:
        execute(
            "INSERT INTO policies (policy_type, target, action, description, enabled) VALUES (?, ?, ?, ?, 1)",
            ('blacklist', src_ip, 'block', f'来自告警 #{alert_id}: {attack_type} 攻击，目标 {alert["dst_ip"]}'),
        )

    # 更新该 IP 的所有告警状态
    execute(
        "UPDATE alerts SET status = 'blocked', updated_at = datetime('now', 'localtime') WHERE src_ip = ?",
        (src_ip,),
    )

    # 审计日志
    log_action('block_ip', f'alert_id={alert_id}, ip={src_ip}, attack={attack_type}')

    return jsonify({'success': True, 'message': f'已拉黑 IP: {src_ip}'})


@app.route('/api/alerts/<int:alert_id>/unblock', methods=['POST'])
def unblock_ip(alert_id):
    """解除拉黑：删除黑名单 policy + 恢复该 IP 所有告警状态"""
    alert = query_one("SELECT id, src_ip FROM alerts WHERE id = ?", (alert_id,))
    if not alert:
        return jsonify({'success': False, 'message': '告警不存在'}), 404

    ip_address = alert['src_ip']

    # 检查该 IP 是否有被拉黑的告警
    blocked_count = query_one(
        "SELECT COUNT(*) as c FROM alerts WHERE src_ip = ? AND status = 'blocked'",
        (ip_address,),
    )['c']
    if blocked_count == 0:
        return jsonify({'success': False, 'message': '该 IP 没有被拉黑的告警'}), 400

    execute("DELETE FROM policies WHERE policy_type = 'blacklist' AND target = ?", (ip_address,))
    execute(
        "UPDATE alerts SET status = 'reviewed', updated_at = datetime('now', 'localtime') WHERE src_ip = ? AND status = 'blocked'",
        (ip_address,),
    )

    log_action('unblock_ip', f'alert_id={alert_id}, ip={ip_address}')

    return jsonify({'success': True, 'message': f'已解除对 {ip_address} 的拉黑'})


@app.route('/api/alerts/<int:alert_id>/trace', methods=['POST'])
def trace_alert(alert_id):
    """溯源分析：生成溯源报告 + 更新告警状态"""
    alert = query_one(
        "SELECT id, src_ip, dst_ip, attack_type, risk_level, confidence, description, created_at "
        "FROM alerts WHERE id = ?",
        (alert_id,),
    )
    if not alert:
        return jsonify({'success': False, 'message': '告警不存在'}), 404

    # 查询同源 IP 的历史告警数
    history = query_one(
        "SELECT COUNT(*) as c FROM alerts WHERE src_ip = ? AND id != ?",
        (alert['src_ip'], alert_id),
    )

    # 查询该 IP 关联的设备信息
    asset = query_one(
        "SELECT name, device_type, mac_address FROM assets WHERE ip_address = ?",
        (alert['src_ip'],),
    )

    # 生成溯源报告
    trace_parts = [
        f"=== 溯源分析报告 ===\n",
        f"告警编号: #{alert['id']}",
        f"攻击来源 IP: {alert['src_ip']}",
        f"攻击目标 IP: {alert['dst_ip']}",
        f"攻击类型: {alert['attack_type']}",
        f"风险等级: {alert['risk_level']}",
        f"置信度: {alert['confidence']*100:.1f}%",
        f"首次发现: {alert['created_at']}",
    ]

    if asset:
        trace_parts.append(f"\n--- 关联设备信息 ---")
        trace_parts.append(f"设备名称: {asset['name']}")
        trace_parts.append(f"设备类型: {asset['device_type']}")
        trace_parts.append(f"MAC 地址: {asset['mac_address'] or '未知'}")

    if history:
        trace_parts.append(f"\n--- 历史关联 ---")
        trace_parts.append(f"同源 IP 历史告警: {history['c']} 条")
        trace_parts.append(f"该 IP 活跃程度: {'频繁' if history['c'] > 10 else '偶发' if history['c'] > 3 else '首次'}")

    # 攻击描述
    trace_parts.append(f"\n--- 攻击描述 ---")
    trace_parts.append(alert['description'] or f'{alert["attack_type"]} 网络攻击行为')

    trace_info = '\n'.join(trace_parts)

    # 更新告警
    execute(
        "UPDATE alerts SET trace_info = ?, status = 'reviewed', updated_at = datetime('now', 'localtime') WHERE id = ?",
        (trace_info, alert_id),
    )

    log_action('trace_alert', f'alert_id={alert_id}, src_ip={alert["src_ip"]}')

    return jsonify({
        'success': True,
        'trace_info': trace_info,
    })


@app.route('/api/alerts/<int:alert_id>/false-positive', methods=['POST'])
def mark_false_positive(alert_id):
    """标记误报：更新告警状态 + 记录审计日志"""
    alert = query_one("SELECT id, src_ip, attack_type FROM alerts WHERE id = ?", (alert_id,))
    if not alert:
        return jsonify({'success': False, 'message': '告警不存在'}), 404

    execute(
        "UPDATE alerts SET status = 'false_positive', updated_at = datetime('now', 'localtime') WHERE id = ?",
        (alert_id,),
    )

    log_action('mark_fp', f'alert_id={alert_id}, ip={alert["src_ip"]}, attack={alert["attack_type"]}')

    return jsonify({'success': True, 'message': f'已将告警 #{alert_id} 标记为误报'})


@app.route('/api/alerts/<int:alert_id>/unmark-false-positive', methods=['POST'])
def unmark_false_positive(alert_id):
    """撤销误报标记：恢复告警状态为 reviewed"""
    alert = query_one("SELECT id, status FROM alerts WHERE id = ?", (alert_id,))
    if not alert:
        return jsonify({'success': False, 'message': '告警不存在'}), 404
    if alert['status'] != 'false_positive':
        return jsonify({'success': False, 'message': '该告警未被标记为误报'}), 400

    execute(
        "UPDATE alerts SET status = 'reviewed', updated_at = datetime('now', 'localtime') WHERE id = ?",
        (alert_id,),
    )

    log_action('unmark_fp', f'alert_id={alert_id}')

    return jsonify({'success': True, 'message': f'已撤销告警 #{alert_id} 的误报标记'})


# ---- Blacklist Management ----
@app.route('/api/blocklist', methods=['GET'])
def blocklist_list():
    """获取所有黑名单记录（关联告警信息）"""
    # 对每个 IP 取风险最高的一条告警作为展示信息
    rows = query_all(
        "SELECT p.id, p.target AS ip_address, p.description AS reason, "
        "p.created_at AS blocked_at, p.enabled, "
        "a.id AS alert_id, a.attack_type, a.risk_level, a.dst_ip, a.status AS alert_status "
        "FROM policies p "
        "LEFT JOIN alerts a ON a.id = ("
        "  SELECT a2.id FROM alerts a2"
        "  WHERE a2.src_ip = p.target"
        "  ORDER BY CASE a2.risk_level WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,"
        "    a2.created_at DESC LIMIT 1"
        ") "
        "WHERE p.policy_type = 'blacklist' "
        "ORDER BY p.created_at DESC"
    )

    # 对同一 IP 的多条记录去重
    seen_ips = set()
    deduped = []
    for r in rows:
        if r['ip_address'] not in seen_ips:
            seen_ips.add(r['ip_address'])
            deduped.append(r)

    return jsonify({'total': len(deduped), 'items': deduped})


@app.route('/api/blocklist/<int:policy_id>', methods=['DELETE'])
def blocklist_delete(policy_id):
    """解除拉黑：删除 policy 记录 + 恢复关联告警状态"""
    policy = query_one(
        "SELECT id, target FROM policies WHERE id = ? AND policy_type = 'blacklist'",
        (policy_id,),
    )
    if not policy:
        return jsonify({'success': False, 'message': '黑名单记录不存在'}), 404

    ip_address = policy['target']

    # 删除该 IP 的所有黑名单 policy
    execute("DELETE FROM policies WHERE policy_type = 'blacklist' AND target = ?", (ip_address,))

    # 恢复该 IP 关联的告警状态
    execute(
        "UPDATE alerts SET status = 'reviewed', updated_at = datetime('now', 'localtime') WHERE src_ip = ? AND status = 'blocked'",
        (ip_address,),
    )

    log_action('unblock_ip', f'policy_id={policy_id}, ip={ip_address}')

    return jsonify({'success': True, 'message': f'已解除对 {ip_address} 的拉黑'})


# ---- Policy Management ----
@app.route('/api/policies', methods=['GET'])
def policies_list():
    """获取策略列表"""
    policy_type = request.args.get('type', '')
    where = "WHERE 1=1"
    params = []
    if policy_type:
        where += " AND policy_type = ?"
        params.append(policy_type)
    rows = query_all(
        f"SELECT * FROM policies {where} ORDER BY created_at DESC",
        params,
    )
    return jsonify({'items': rows})


@app.route('/api/policies', methods=['POST'])
def policies_create():
    """新增策略"""
    data = request.get_json() or {}
    policy_type = data.get('policy_type', 'blacklist')
    target = data.get('target', '')
    action = data.get('action', 'alert')
    description = data.get('description', '')
    enabled = data.get('enabled', 1)
    if not target:
        return jsonify({'success': False, 'message': '目标不能为空'}), 400
    pid = execute(
        "INSERT INTO policies (policy_type, target, action, description, enabled) VALUES (?,?,?,?,?)",
        (policy_type, target, action, description, enabled),
    )
    log_action('create_policy', f'id={pid}, type={policy_type}, target={target}')
    return jsonify({'success': True, 'id': pid})


@app.route('/api/policies/<int:policy_id>', methods=['PUT'])
def policies_update(policy_id):
    """编辑策略"""
    data = request.get_json() or {}
    execute(
        "UPDATE policies SET policy_type=?, target=?, action=?, description=?, enabled=? WHERE id=?",
        (data.get('policy_type'), data.get('target'), data.get('action'),
         data.get('description', ''), data.get('enabled', 1), policy_id),
    )
    log_action('update_policy', f'id={policy_id}')
    return jsonify({'success': True})


@app.route('/api/policies/<int:policy_id>', methods=['DELETE'])
def policies_delete(policy_id):
    """删除策略"""
    execute("DELETE FROM policies WHERE id = ?", (policy_id,))
    log_action('delete_policy', f'id={policy_id}')
    return jsonify({'success': True})


# ---- Asset Management ----
@app.route('/api/assets', methods=['GET'])
def assets_list():
    """获取设备列表"""
    rows = query_all("SELECT * FROM assets ORDER BY created_at DESC")
    return jsonify({'items': rows})


@app.route('/api/assets', methods=['POST'])
def assets_create():
    """新增设备"""
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    ip = data.get('ip_address', '').strip()
    if not name or not ip:
        return jsonify({'success': False, 'message': '设备名和IP不能为空'}), 400
    aid = execute(
        "INSERT INTO assets (name, ip_address, mac_address, device_type, status, risk_level, last_seen) VALUES (?,?,?,?,?,?,datetime('now','localtime'))",
        (name, ip, data.get('mac_address', ''), data.get('device_type', 'other'),
         data.get('status', 'online'), data.get('risk_level', 'low')),
    )
    return jsonify({'success': True, 'id': aid})


@app.route('/api/assets/<int:asset_id>', methods=['PUT'])
def assets_update(asset_id):
    """编辑设备"""
    data = request.get_json() or {}
    execute(
        "UPDATE assets SET name=?, ip_address=?, mac_address=?, device_type=?, status=?, risk_level=?, last_seen=datetime('now','localtime') WHERE id=?",
        (data.get('name'), data.get('ip_address'), data.get('mac_address', ''),
         data.get('device_type'), data.get('status'), data.get('risk_level'), asset_id),
    )
    return jsonify({'success': True})


@app.route('/api/assets/<int:asset_id>', methods=['DELETE'])
def assets_delete(asset_id):
    """删除设备"""
    execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    return jsonify({'success': True})


# ---- Log Center ----
@app.route('/api/logs/audit')
def logs_audit():
    """审计日志"""
    rows = query_all(
        "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 200"
    )
    return jsonify({'items': rows})


@app.route('/api/logs/traffic')
def logs_traffic():
    """流量日志"""
    rows = query_all(
        "SELECT * FROM traffic_logs ORDER BY timestamp DESC LIMIT 200"
    )
    return jsonify({'items': rows})


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
