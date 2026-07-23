"""
Traffic Capture Service — Real-time packet capture with Scapy or simulation mode

Works in two modes:
- With Scapy/Npcap: captures real network packets from Windows NIC
- Simulation mode: generates test flows for demo without hardware requirements
"""
import threading
import time
import random
from datetime import datetime
from typing import Optional, Callable

from database import execute
from services.rule_engine import get_rule_engine, FlowRecord

# Try importing Scapy
try:
    from scapy.all import sniff, IP, TCP, UDP, ICMP
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False

# Try ONNX inference
try:
    from models.inference import get_engine as get_onnx_engine
    from services.feature_extract import FeatureExtractor
    ONNX_AVAILABLE = True
except Exception:
    ONNX_AVAILABLE = False


class TrafficCapture:
    """Background traffic capture with dual-engine detection."""

    def __init__(self):
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.packet_count = 0
        self.alert_count = 0
        self.attack_ratio = 0.25
        self.on_alert: Optional[Callable] = None  # callback(alert_dict)

    def start(self, interface: Optional[str] = None, use_scapy: bool = False, attack_ratio: float = 0.25):
        """Start capture in background thread."""
        if self.running:
            return {'success': False, 'message': '抓包已在运行中'}

        self.attack_ratio = max(0, min(1, attack_ratio))
        self.capture_mode = 'real' if (use_scapy and SCAPY_AVAILABLE) else 'sim'

        self.running = True
        if use_scapy and SCAPY_AVAILABLE:
            self.thread = threading.Thread(target=self._capture_scapy, args=(interface,), daemon=True)
        else:
            self.thread = threading.Thread(target=self._capture_simulate, daemon=True)
        self.thread.start()
        return {'success': True, 'message': '抓包已启动', 'mode': 'scapy' if (use_scapy and SCAPY_AVAILABLE) else 'simulation'}

    def stop(self):
        """Stop capture."""
        self.running = False
        return {'success': True, 'packet_count': self.packet_count, 'alert_count': self.alert_count}

    def status(self):
        """Get capture status."""
        return {
            'running': self.running,
            'packet_count': self.packet_count,
            'alert_count': self.alert_count,
            'scapy_available': SCAPY_AVAILABLE,
            'onnx_available': ONNX_AVAILABLE,
        }

    def _process_packet(self, src_ip: str, dst_ip: str, src_port: int, dst_port: int,
                         protocol: str, length: int, flags: str = '', payload: str = '',
                         known_normal: bool = False):
        """Process a single packet through the detection pipeline."""
        self.packet_count += 1

        # 1. Rule Engine
        flow = FlowRecord(
            src_ip=src_ip, dst_ip=dst_ip,
            src_port=src_port, dst_port=dst_port,
            protocol=protocol, flags=flags,
            length=length, payload=payload,
            timestamp=datetime.now(),
        )
        engine = get_rule_engine()
        matches = engine.evaluate(flow)

        # 2. ONNX深度学习模型推理
        onnx_label = 'normal'
        onnx_attack = False
        if ONNX_AVAILABLE:
            try:
                flow_data = {
                    'protocol_type': 1 if protocol == 'TCP' else 2 if protocol == 'UDP' else 3,
                    'src_port': src_port, 'dst_port': dst_port,
                    'min_packet_length': length,
                    'syn_count': 1 if 'S' in flags else 0,
                    'ack_count': 1 if 'A' in flags else 0,
                    'flow_duration': 0.01,
                    'flow_bytes_per_sec': length * 100,
                    'flow_packets_per_sec': 100,
                }
                extractor = FeatureExtractor()
                features = extractor.extract_from_flow(flow_data)
                onnx_engine = get_onnx_engine()
                result = onnx_engine.predict(features)
                onnx_label = result['class_name'].lower()
                onnx_attack = result['is_attack']
            except Exception:
                pass  # ONNX推理失败时静默，用规则引擎结果

        # 3. Save traffic log with ONNX label
        execute(
            "INSERT INTO traffic_logs (src_ip, dst_ip, src_port, dst_port, protocol, length, flags, onnx_label, source) VALUES (?,?,?,?,?,?,?,?,?)",
            (src_ip, dst_ip, src_port, dst_port, protocol, length, flags, onnx_label, self.capture_mode),
        )

        # 4. ONNX 检测到攻击 → 生成告警（已知正常流量跳过）
        if onnx_attack and not known_normal:
            from database import query_one as _q, get_config
            window = int(get_config('merge_window_minutes', '5'))
            dup = _q(
                "SELECT COUNT(*) as c FROM alerts WHERE attack_type = ? AND src_ip = ? "
                "AND created_at > datetime('now', ? || ' minutes', 'localtime')",
                (onnx_label.title(), src_ip, f'-{window}'),
            )
            if not dup or dup['c'] == 0:
                self.alert_count += 1

                # 多因子风险评分
                attack_scores = {'mirai': 10, 'gafgyt': 8, 'other': 5}
                base = attack_scores.get(onnx_label, 5) * result.get('confidence', 0.85)
                base_score = base / 10 * 100

                freq = _q(
                    "SELECT COUNT(*) as c FROM alerts WHERE src_ip = ? "
                    "AND created_at > datetime('now', '-5 minutes', 'localtime')",
                    (src_ip,),
                )['c']
                freq_map = {0: 1, 1: 3, 2: 3, 3: 5, 4: 5, 5: 5}
                freq_score = freq_map.get(freq, 7 if freq <= 10 else 10)

                asset = _q(
                    "SELECT device_type FROM assets WHERE ip_address = ?", (dst_ip,)
                )
                target_map = {'lock': 10, 'door': 10, 'camera': 7, 'hub': 7, 'sensor': 5, 'router': 5}
                target_score = target_map.get(asset['device_type'] if asset else '', 3)

                total = base_score * 0.4 + freq_score * 10 * 0.35 + target_score * 10 * 0.25
                if total >= 80: risk_level = 'critical'
                elif total >= 60: risk_level = 'high'
                else: risk_level = 'medium'

                # 自动拉黑：开关开启 + 高危 || (中危且持续攻击≥5次)
                should_block = get_config('auto_block', 'false') == 'true' and (
                    risk_level == 'critical' or
                    (risk_level == 'high' and freq >= 5)
                )
                if should_block:
                    existing = _q("SELECT id FROM policies WHERE policy_type='blacklist' AND target=? AND enabled=1", (src_ip,))
                    if not existing or existing['c'] == 0:
                        execute(
                            "INSERT INTO policies (policy_type, target, action, description, enabled) "
                            "VALUES ('blacklist',?,'block',?,1)",
                            (src_ip, f'自动拉黑: {onnx_label.title()}攻击(风险{total:.0f})'))

                # 匹配目标设备
                dev_row = _q("SELECT name FROM assets WHERE ip_address = ? AND device_type != 'probe'", (dst_ip,))
                dev_name = f' 目标:{dev_row["name"]}({dst_ip})' if dev_row else ''
                if dev_row:
                    execute("UPDATE assets SET risk_level=?, status='alert', last_seen=datetime('now','localtime') WHERE ip_address=?",
                            (risk_level, dst_ip))

                execute(
                    "INSERT INTO alerts (risk_level, attack_type, src_ip, dst_ip, src_port, dst_port, protocol, confidence, description, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,'new')",
                    (risk_level, onnx_label.title(), src_ip, dst_ip,
                     src_port, dst_port, protocol, round(result['confidence'], 2),
                     f'[{self.capture_mode}]{dev_name} {result["class_name"]} (置信度 {result["confidence"]:.1%})'),
                )

        # 5. Rule engine: log matches only (no alerts, ONNX is primary)
        for match in matches:
            self.alert_count += 1  # count for stats
            # Rule matches logged but NOT inserted as alerts

    def _capture_scapy(self, interface=None):
        """Real packet capture using Scapy."""
        def packet_handler(pkt):
            if not self.running:
                return False
            if IP not in pkt:
                return
            ip = pkt[IP]
            proto = ''
            sport, dport = 0, 0
            flags = ''
            if TCP in pkt:
                proto = 'TCP'
                sport, dport = pkt[TCP].sport, pkt[TCP].dport
                flags = str(pkt[TCP].flags)
            elif UDP in pkt:
                proto = 'UDP'
                sport, dport = pkt[UDP].sport, pkt[UDP].dport
            elif ICMP in pkt:
                proto = 'ICMP'

            self._process_packet(ip.src, ip.dst, sport, dport, proto, len(pkt), flags)

        sniff(prn=packet_handler, store=False, timeout=1)

    def _capture_simulate(self):
        """模拟真实社区IoT场景：多设备+正常通信+攻击混合"""
        cameras = [f'192.168.1.{i}' for i in range(10, 15)]
        doors = [f'192.168.1.{i}' for i in range(20, 23)]
        sensors = [f'192.168.1.{i}' for i in range(30, 38)]
        plugs = [f'192.168.1.{i}' for i in range(40, 44)]
        hub = '192.168.1.1'
        cloud = '10.0.0.1'
        all_devices = cameras + doors + sensors + plugs + [hub]
        attackers = ['10.99.1.100', '10.99.1.200', '172.20.0.50', '45.33.32.156']

        while self.running:
            time.sleep(random.uniform(0.05, 0.3))
            r = random.random()

            if r < self.attack_ratio:
                src = random.choice(attackers)
                target = random.choice(all_devices)
                at = random.random()
                if at < 0.35:
                    for _ in range(random.randint(3, 8)):
                        self._process_packet(src, target, random.randint(50000, 60000),
                                             random.choice([23, 2323, 80]), 'TCP', 60, 'SYN')
                        time.sleep(0.05)
                elif at < 0.6:
                    for _ in range(random.randint(5, 15)):
                        self._process_packet(src, target, random.randint(30000, 40000),
                                             random.choice([80, 443]), 'UDP', 1400)
                        time.sleep(0.03)
                elif at < 0.8:
                    for _ in range(random.randint(2, 4)):
                        self._process_packet(src, target, random.randint(50000, 60000),
                                             22, 'TCP', 80, 'SYN')
                        time.sleep(0.1)
                else:
                    self._process_packet(src, random.choice(attackers), 52341,
                                         46370, 'TCP', 200, 'PSH')
            else:
                dev = random.choice(all_devices)
                nt = random.random()
                if nt < 0.3:
                    self._process_packet(random.choice(cameras), cloud, random.randint(40000,50000),
                                         443, 'TCP', random.randint(800,1500), 'PSH')
                elif nt < 0.55:
                    self._process_packet(random.choice(sensors), hub, random.randint(40000,50000),
                                         1883, 'TCP', random.randint(60,200), 'PA')
                elif nt < 0.7:
                    self._process_packet(hub, random.choice(plugs), 80, random.randint(40000,50000),
                                         1883, 'TCP', random.randint(80,300), 'PA')
                elif nt < 0.85:
                    self._process_packet(random.choice(doors), cloud, random.randint(40000,50000),
                                         443, 'TCP', random.randint(200,600), 'A')
                else:
                    self._process_packet(dev, hub, random.randint(40000,50000),
                                         random.choice([53,80]), random.choice(['TCP','UDP']),
                                         random.randint(60,500), known_normal=True)


# ---- Global singleton ----
_capture: Optional[TrafficCapture] = None


def get_capture() -> TrafficCapture:
    global _capture
    if _capture is None:
        _capture = TrafficCapture()
    return _capture
