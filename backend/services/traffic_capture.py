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
                         protocol: str, length: int, flags: str = '', payload: str = ''):
        """Process a single packet through the dual-engine detection pipeline."""
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
            "INSERT INTO traffic_logs (src_ip, dst_ip, src_port, dst_port, protocol, length, flags, onnx_label) VALUES (?,?,?,?,?,?,?,?)",
            (src_ip, dst_ip, src_port, dst_port, protocol, length, flags, onnx_label),
        )

        # 4. ONNX 检测到攻击 → 生成告警（同攻击+同IP 5分钟内不重复）
        if onnx_attack:
            from database import query_one as _q
            dup = _q(
                "SELECT COUNT(*) as c FROM alerts WHERE attack_type = ? AND src_ip = ? "
                "AND created_at > datetime('now', '-5 minutes', 'localtime')",
                (onnx_label.title(), src_ip),
            )
            if not dup or dup['c'] == 0:
                self.alert_count += 1
                risk_level = 'critical' if onnx_label in ('mirai', 'gafgyt') else 'high'
                execute(
                    "INSERT INTO alerts (risk_level, attack_type, src_ip, dst_ip, src_port, dst_port, protocol, confidence, description, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,'new')",
                    (risk_level, onnx_label.title(), src_ip, dst_ip,
                     src_port, dst_port, protocol, round(result['confidence'], 2),
                     f'[ONNX] 深度学习检测: {result["class_name"]} (置信度 {result["confidence"]:.1%})'),
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
        """Simulated capture mode — generates realistic test traffic."""
        src_ips = [f'192.168.1.{i}' for i in range(10, 50)]
        attacker_ips = ['10.99.1.100', '10.99.1.200', '172.20.0.50']

        while self.running:
            time.sleep(random.uniform(0.3, 2.0))

            is_attack = random.random() < self.attack_ratio
            src = random.choice(attacker_ips) if is_attack else random.choice(src_ips)
            dst = '192.168.1.1'

            if is_attack:
                # Simulate various attack patterns
                attack_type = random.choice(['mirai_scan', 'mirai_flood', 'gafgyt_udp', 'c2'])
                if attack_type == 'mirai_scan':
                    self._process_packet(src, dst, random.randint(50000, 60000),
                                         random.randint(1, 1000), 'TCP', 60, 'SYN')
                elif attack_type == 'mirai_flood':
                    for _ in range(random.randint(3, 8)):
                        self._process_packet(src, dst, random.randint(30000, 40000),
                                             80, 'UDP', 1400)
                elif attack_type == 'gafgyt_udp':
                    for _ in range(random.randint(2, 5)):
                        self._process_packet(src, dst, random.randint(50000, 60000),
                                             22, 'TCP', 80, 'SYN')
                        time.sleep(0.1)
                elif attack_type == 'c2':
                    self._process_packet(src, '172.20.0.50', 52341,
                                         46370, 'TCP', 200, 'PSH', payload='/bin/sh')
            else:
                # Normal IoT traffic
                normal_ports = [80, 443, 1883, 5683, 53]
                self._process_packet(src, dst, random.randint(40000, 50000),
                                     random.choice(normal_ports),
                                     random.choice(['TCP', 'UDP']),
                                     random.randint(60, 800))


# ---- Global singleton ----
_capture: Optional[TrafficCapture] = None


def get_capture() -> TrafficCapture:
    global _capture
    if _capture is None:
        _capture = TrafficCapture()
    return _capture
