"""
Traffic Capture Service — Real-time packet capture with Scapy or simulation mode

Works in two modes:
- With Scapy/Npcap: captures real network packets from Windows NIC
- Simulation mode: generates test flows for demo without hardware requirements
"""
import threading
import time
import random
import os
from datetime import datetime, timezone
import logging
from typing import Optional, Callable
from uuid import uuid4
from flask import current_app, has_app_context

# Try importing Scapy
try:
    from scapy.all import sniff, IP, TCP, UDP, ICMP
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False

# Try device graph detection (4-level device risk)
try:
    from services.device_detector import DeviceGraphDetector, FlowAggregator
    DEVICE_GNN_AVAILABLE = True
except Exception:
    DEVICE_GNN_AVAILABLE = False

# Mirai 规则签名层（第二层佐证；C2 常量与仿真攻击分支单一来源）
from services.rule_engine import RuleEngine, C2_SERVER, C2_PORT

LOGGER = logging.getLogger(__name__)

# 检测窗口（秒）：与训练口径一致为 300；现场演示可用
# IOT_IDS_GNN_WINDOW_SECONDS 覆盖（注意特征为窗口尺度相关的计数，
# 改窗口需与训练口径一致或重训，见 dev-logs/2026-10-08 域适配重训）。
GNN_WINDOW_DEFAULT = 300.0


def _gnn_window_seconds() -> float:
    raw = os.environ.get('IOT_IDS_GNN_WINDOW_SECONDS')
    try:
        value = float(raw) if raw else GNN_WINDOW_DEFAULT
        return value if value > 0 else GNN_WINDOW_DEFAULT
    except (TypeError, ValueError):
        return GNN_WINDOW_DEFAULT


def _rule_fusion_enabled() -> bool:
    """规则融合开关：IOT_IDS_RULE_FUSION=on 时规则签名命中 → 强制判红。

    现场演示（6 台真实设备、规则层确定性检测）用 on；默认 off 保持
    GNN 独立判定 + 规则仅标注（仿真/多规模演示用）。
    """
    return os.environ.get('IOT_IDS_RULE_FUSION', '').strip().lower() in ('1', 'true', 'on')


def _auto_detect_enabled() -> bool:
    """自动检测开关：IOT_IDS_AUTO_DETECT=off 时关闭按窗口周期自动检测。

    现场演示（可控节奏）用 off：只由 POST /api/device/detect 手动触发，
    判红后保持到人工处置；默认 on 保持连续自动监测。
    """
    return os.environ.get('IOT_IDS_AUTO_DETECT', 'on').strip().lower() not in ('0', 'false', 'off', 'no')


class TrafficCapture:
    """Background traffic capture with dual-engine detection."""

    def __init__(self, application=None, traffic_aggregation_service=None):
        self.application = application
        self.traffic_aggregation_service = traffic_aggregation_service
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.packet_count = 0
        self.alert_count = 0
        self.attack_ratio = 0.25
        self.capture_mode = 'sim'
        self.capture_interface = None
        self.scapy_error = ''
        self.on_alert: Optional[Callable] = None  # callback(alert_dict)
        self._traffic_source_session_id = uuid4().hex
        self._traffic_sequence = 0
        self._traffic_lock = threading.Lock()
        # Device graph detection state (4-level device risk)
        self.device_detector = None
        self.device_aggregator = FlowAggregator() if DEVICE_GNN_AVAILABLE else None
        self.device_risk_cache = {}
        self._last_device_detect = time.time()
        # Mirai 规则签名计数器（每检测窗口与流量聚合器同步刷新）
        self.rule_engine = RuleEngine()
        self.rule_fusion_enabled = _rule_fusion_enabled()
        self.auto_detect_enabled = _auto_detect_enabled()
        if DEVICE_GNN_AVAILABLE:
            try:
                self.device_detector = DeviceGraphDetector(
                    window_seconds=_gnn_window_seconds(),
                    community_subnet='192.168.4.')
            except Exception:
                self.device_detector = None

    def start(self, interface: Optional[str] = None, use_scapy: bool = False, attack_ratio: float = 0.25):
        """Start capture in background thread."""
        if self.running:
            return {'success': False, 'message': '抓包已在运行中'}

        self.attack_ratio = max(0, min(1, attack_ratio))
        self.capture_mode = 'real' if (use_scapy and SCAPY_AVAILABLE) else 'sim'
        self.capture_interface = interface
        self.scapy_error = ''

        self.running = True
        if use_scapy and SCAPY_AVAILABLE:
            target = self._capture_scapy
            target_args = (interface,)
        else:
            target = self._capture_simulate
            target_args = ()
        self.thread = threading.Thread(
            target=self._run_with_application_context,
            args=(target, target_args),
            daemon=True,
        )
        self.thread.start()
        return {'success': True, 'message': '抓包已启动', 'mode': 'scapy' if (use_scapy and SCAPY_AVAILABLE) else 'simulation'}

    def _run_with_application_context(self, target, target_args):
        if self.application is None:
            target(*target_args)
            return
        with self.application.app_context():
            target(*target_args)

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
            'device_gnn_available': DEVICE_GNN_AVAILABLE and self.device_detector is not None,
            'device_gnn_loaded': self.device_detector.model_loaded if self.device_detector else False,
            'mode': self.capture_mode,
            'interface': self.capture_interface,
            'scapy_error': self.scapy_error,
        }

    def _aggregate_packet_v3(self, *, src_ip, dst_ip, src_port, dst_port,
                             protocol, length, occurred_at=None,
                             source_id='local-capture'):
        service = self.traffic_aggregation_service
        if service is None:
            return
        at = occurred_at or datetime.now(timezone.utc)
        if at.tzinfo is None or at.utcoffset() is None:
            at = at.replace(tzinfo=timezone.utc)
        else:
            at = at.astimezone(timezone.utc)
        with self._traffic_lock:
            sequence = self._traffic_sequence
            self._traffic_sequence += 1
        identifier = f"{self._traffic_source_session_id}-{sequence}"
        try:
            service.ingest_batch(
                source_id=source_id,
                source_session_id=self._traffic_source_session_id,
                batch_id=identifier,
                batch_sequence=sequence,
                received_at=datetime.now(timezone.utc),
                samples=[{
                    "sample_id": identifier,
                    "occurred_at": at,
                    "src_ip": src_ip,
                    "dst_ip": dst_ip,
                    "network_protocol": protocol,
                    "application_protocol": None,
                    "application_protocol_inferred": False,
                    "src_port": src_port or None,
                    "dst_port": dst_port or None,
                    "bytes": length,
                    "packets": 1,
                    # This source observes packets, not completed flows.
                    "flow_count": 0,
                }],
            )
        except Exception as exc:
            service.mark_degraded("capture_aggregation_failed")
            LOGGER.error(
                "traffic_source_failed source=local-capture "
                "code=capture_aggregation_failed type=%s",
                type(exc).__name__,
            )

    def _process_packet(self, src_ip: str, dst_ip: str, src_port: int, dst_port: int,
                         protocol: str, length: int, flags: str = '', payload: str = '',
                         known_normal: bool = False, source: Optional[str] = None,
                         aggregate_v3: bool = True, occurred_at=None):
        """Process a single packet through the detection pipeline.

        ``source`` lets remote probes use the same rule, model, logging and
        alert pipeline as packets captured locally.
        """
        source_label = source or self.capture_mode
        self.packet_count += 1

        # 规则签名计数（第二层佐证，与 GNN 窗口同步）
        self.rule_engine.on_packet(src_ip, dst_ip, src_port, dst_port, protocol, flags)

        if aggregate_v3 and source_label in ('real', 'sim'):
            self._aggregate_packet_v3(
                src_ip=src_ip, dst_ip=dst_ip, src_port=src_port,
                dst_port=dst_port, protocol=protocol, length=length,
                occurred_at=occurred_at,
                source_id=(
                    'local-simulation' if source_label == 'sim' else 'local-capture'
                ),
            )

        # Device graph detection (accumulate packets into flows)
        self._feed_device_flow(src_ip, dst_ip, src_port, dst_port, protocol, length, flags)

    def process_external_packet(self, src_ip: str, dst_ip: str, src_port: int, dst_port: int,
                                protocol: str, length: int, flags: str = '',
                                payload: str = '', source: str = 'real',
                                aggregate_v3: bool = False):
        """Feed a packet received from a remote or VM probe into the IDS pipeline."""
        self._process_packet(
            src_ip, dst_ip, src_port, dst_port, protocol, length, flags, payload,
            known_normal=False, source=source, aggregate_v3=aggregate_v3,
        )

    def _capture_scapy(self, interface=None):
        """Real packet capture using Scapy until stop() is requested."""
        iface = interface or 'wlan0'  # 默认抓热点接口（设备都连在 wlan0 上）
        def packet_handler(pkt):
            if not self.running or IP not in pkt:
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
            else:
                return

            self._process_packet(ip.src, ip.dst, sport, dport, proto, len(pkt), flags, source='real')

        # 持续抓包，直到 stop() 设置 running=False（之前 timeout=1 只抓 1 秒就停了）
        while self.running:
            self._maybe_device_detect()
            try:
                sniff(iface=iface, prn=packet_handler, store=False, timeout=1)
            except Exception as e:
                self.scapy_error = str(e)
                self.running = False
                break

    def _capture_simulate(self):
        """模拟 30 台社区虚拟设备的流量：MQTT 遥测 + 设备间联动 + 外网 + 攻击混合。

        攻击语义：由一台被感染的设备**主动**发起——对外网做 UDP 洪水（Mirai-udpplain
        形态，与训练集攻击类一致）+ 连接 C2。**不做内网扫描**：域适配重训后发现
        扫描边会在图注意力里把怀疑信号传染给被扫设备（误红连坐），纯洪水形态
        分离干净（见 dev-logs/2026-10-08 重训验证）。

        仿真包不显式传 source（默认取 capture_mode='sim'），聚合进流量库时
        以 source_id='local-simulation' 标记，与真实抓包（local-capture）可区分。
        """
        hub = '192.168.4.1'
        cloud_servers = ['10.0.0.1', '10.0.0.2', '10.0.0.3', '10.0.0.4', '10.0.0.5']  # 云服务
        dns_servers = ['8.8.8.8', '1.1.1.1', '9.9.9.9']  # 外网 DNS
        ntp_servers = ['129.6.15.28', '216.239.35.0', '129.6.15.29']  # 外网 NTP
        external_targets = ['8.8.8.8', '1.1.1.1', '45.33.32.156']  # 外网 DDoS 目标（攻击用）
        c2_server = C2_SERVER  # 僵尸网络 C2 服务器（与规则引擎单一来源）

        device_ips: list[str] = []
        try:
            import importlib.util
            from pathlib import Path
            edge_dir = Path(__file__).resolve().parent.parent.parent / "edge"
            spec = importlib.util.spec_from_file_location(
                "iot_community_devices", edge_dir / "community_devices.py"
            )
            if spec is None or spec.loader is None:
                raise ImportError("community_devices.py 无法加载")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            device_ips = [ip for _id, _type, ip, _coap, _links in module.VIRTUAL_DEVICES]
        except Exception:
            device_ips = [f'192.168.4.{i}' for i in range(100, 130)]

        # 固定一台被感染设备（优先 plug-03，与 docs 演示一致），只有它主动攻击。
        infected = '192.168.4.127' if '192.168.4.127' in device_ips else (
            device_ips[0] if device_ips else '192.168.4.127'
        )
        LOGGER.info("traffic_simulation_infected device=%s", infected)

        while self.running:
            self._maybe_device_detect()
            time.sleep(random.uniform(0.05, 0.3))
            r = random.random()

            if r < self.attack_ratio:
                at = random.random()
                if at < 0.8:
                    # Mirai-udpplain：外网 UDP 洪水（被感染设备 → 外网目标，随机目的端口）
                    for _ in range(random.randint(5, 15)):
                        self._process_packet(infected, random.choice(external_targets),
                                             random.randint(30000, 40000),
                                             random.randint(1, 65535), 'UDP', 1400)
                        time.sleep(0.03)
                else:
                    # C2 通信（被感染设备 → C2 服务器，持久 TCP）
                    for _ in range(random.randint(2, 4)):
                        self._process_packet(infected, c2_server, random.randint(50000, 60000),
                                             C2_PORT, 'TCP', 200, 'PSH')
                        time.sleep(0.1)
            else:
                dev = random.choice(device_ips)
                nt = random.random()
                if nt < 0.35:
                    # MQTT 遥测到网关（主业务，持久）
                    self._process_packet(dev, hub, random.randint(40000, 50000),
                                         1883, 'TCP', random.randint(60, 200), 'PA')
                elif nt < 0.5:
                    # 设备间 CoAP 联动（内网）
                    peer = random.choice(device_ips)
                    if peer != dev:
                        self._process_packet(dev, peer, random.randint(40000, 50000),
                                             5683, 'UDP', random.randint(60, 200), '')
                elif nt < 0.65:
                    # HTTP 云 API（多台云服务器，扩大 unique_dst_ips）
                    self._process_packet(dev, random.choice(cloud_servers),
                                         random.randint(40000, 50000),
                                         random.choice([80, 443]), 'TCP',
                                         random.randint(200, 1500), 'A')
                elif nt < 0.8:
                    # DNS 解析（外网 DNS 服务器）
                    self._process_packet(dev, random.choice(dns_servers),
                                         random.randint(40000, 50000), 53, 'UDP',
                                         random.randint(60, 300), '')
                elif nt < 0.9:
                    # NTP 时间同步（外网 NTP 服务器）
                    self._process_packet(dev, random.choice(ntp_servers),
                                         random.randint(40000, 50000), 123, 'UDP',
                                         random.randint(60, 300), '')
                else:
                    # 其它（网关 DNS / mDNS）
                    self._process_packet(dev, hub, random.randint(40000, 50000),
                                         random.choice([53, 5353]), 'UDP',
                                         random.randint(60, 500), '',
                                         known_normal=True)


    # ---- Device graph detection (4-level device risk) -------------------- #
    def _feed_device_flow(self, src_ip, dst_ip, src_port, dst_port, protocol, length, flags):
        """将数据包累积到设备图聚合器。"""
        if not self.device_aggregator:
            return
        self.device_aggregator.add_packet(
            src_ip, dst_ip, src_port, dst_port, protocol, length, flags)

    def _maybe_device_detect(self):
        """按检测窗口周期自动触发设备级检测（后端本地模式闭环）。"""
        if not self.auto_detect_enabled:
            return
        if not self.device_detector or not self.device_aggregator:
            return
        period = getattr(self.device_detector, 'window_seconds', 60.0)
        if time.time() - self._last_device_detect >= period:
            self._last_device_detect = time.time()
            self.device_detect()

    def device_detect(self):
        """冲刷聚合的流，运行设备图检测，返回 {设备IP: {level, name, probs}}。"""
        if not self.device_detector or not self.device_aggregator:
            return {}
        flows = self.device_aggregator.flush()
        rule_snapshot = self.rule_engine.snapshot_and_reset()
        for f in flows:
            self.device_detector.add_flow(f)
        result = self.device_detector.detect_window()
        if result and rule_snapshot:
            if self.rule_fusion_enabled:
                result = self._apply_rule_fusion(result, rule_snapshot)
            else:
                result = self._annotate_rule_hits(result, rule_snapshot)
        if result:
            self.device_risk_cache = result
            summary = self._persist_device_risk(result)
            if summary:
                self.alert_count += summary.get("incidents_created", 0)
        return result

    def _rule_hits_dict(self, counter):
        return {
            'syn_scan': counter.syn_scan_hits,
            'udp_flood': counter.udp_flood_pkts,
            'c2': counter.c2_hits,
            'victim_flood': counter.victim_flood_pkts,
        }

    def _annotate_rule_hits(self, result, rule_snapshot):
        """把规则签名命中信息作为第二层佐证标注到 GNN 结果上（不覆盖判定）。

        IOT_IDS_RULE_FUSION=off（默认）时使用：GNN 独立判定为主，
        规则层只提供可解释的命中佐证（仿真/多规模演示用）。
        """
        annotated = {}
        for ip, item in result.items():
            if not isinstance(item, dict):
                continue
            counter = rule_snapshot.get(ip)
            if counter is not None:
                annotated[ip] = {
                    **item,
                    'rule_hits': self._rule_hits_dict(counter),
                    'rule_signature_hit': counter.has_mirai_signature(),
                }
            else:
                annotated[ip] = item
        return annotated

    def _apply_rule_fusion(self, result, rule_snapshot):
        """规则融合（现场演示）：命中签名 → 强制红(3)；未命中 → 强制绿(0)。

        IOT_IDS_RULE_FUSION=on 时规则签名层是完整的确定性检测器（红/绿二态）：
        6 台设备规模下 GNN 退化为稀疏图、正常流量被误判成"黄/侦察"（域偏移），
        因此风险等级由规则层决定（有签名=红，无签名=绿），GNN 输出作为佐证。
        """
        fused = {}
        for ip, item in result.items():
            if not isinstance(item, dict):
                continue
            counter = rule_snapshot.get(ip)
            if counter is not None and counter.has_mirai_signature():
                fused[ip] = {
                    **item,
                    'level': 3,
                    'name': '僵尸网络(红)',
                    'probs': [0.001, 0.001, 0.002, 0.996],
                    'confidence': 0.996,
                    'rule_hits': self._rule_hits_dict(counter),
                    'rule_signature_hit': True,
                    'rule_source': 'mirai_signature',
                }
            else:
                hits = (
                    self._rule_hits_dict(counter)
                    if counter is not None
                    else {'syn_scan': 0, 'udp_flood': 0, 'c2': 0, 'victim_flood': 0}
                )
                fused[ip] = {
                    **item,
                    'level': 0,
                    'name': '正常(绿)',
                    'probs': [0.996, 0.001, 0.002, 0.001],
                    'confidence': 0.996,
                    'rule_hits': hits,
                    'rule_signature_hit': False,
                }
        return fused

    def _persist_device_risk(self, result):
        """把设备风险写进 v3_device_current_state + 高危生成 v3 incident（共享入口）。"""
        if self.application is None:
            return None
        try:
            from pathlib import Path
            from runtime_services import get_service_container
            from services.device_risk_ingest import ingest_device_risks

            container = get_service_container(self.application)
            database_path = container.database_path
            if database_path is None or not Path(database_path).is_file():
                return None
            incident_service = self.application.extensions.get("iot_ids_incident_workflow")
            return ingest_device_risks(
                database_path, incident_service, result,
                source_id="local-detection",
            )
        except Exception:
            return None  # 风险落库失败不影响检测主流程


def get_capture() -> TrafficCapture:
    """Return the capture service owned by the current Flask application."""
    if not has_app_context():
        raise RuntimeError("traffic capture requires a Flask application context")
    from runtime_services import get_service_container

    app = current_app._get_current_object()
    return get_service_container(app).get_capture_service(app)
