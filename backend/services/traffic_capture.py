"""
Traffic Capture Service — Real-time packet capture with Scapy or simulation mode

Works in two modes:
- With Scapy/Npcap: captures real network packets from Windows NIC
- Simulation mode: generates test flows for demo without hardware requirements
"""
import threading
import time
import random
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

LOGGER = logging.getLogger(__name__)


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
        if DEVICE_GNN_AVAILABLE:
            try:
                self.device_detector = DeviceGraphDetector(
                    window_seconds=300, community_subnet='192.168.4.')
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
                             protocol, length, occurred_at=None):
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
                source_id="local-capture",
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

        if aggregate_v3 and source_label == 'real':
            self._aggregate_packet_v3(
                src_ip=src_ip, dst_ip=dst_ip, src_port=src_port,
                dst_port=dst_port, protocol=protocol, length=length,
                occurred_at=occurred_at,
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
        """模拟 30 台社区虚拟设备的流量：MQTT 遥测 + 设备间联动 + 外网 + 攻击混合。"""
        hub = '192.168.4.1'
        cloud = '10.0.0.1'
        attackers = ['10.99.1.100', '10.99.1.200', '172.20.0.50', '45.33.32.156']

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

        while self.running:
            self._maybe_device_detect()
            time.sleep(random.uniform(0.05, 0.3))
            r = random.random()

            if r < self.attack_ratio:
                src = random.choice(attackers)
                target = random.choice(device_ips)
                at = random.random()
                if at < 0.35:
                    for _ in range(random.randint(3, 8)):
                        self._process_packet(src, target, random.randint(50000, 60000),
                                             random.choice([23, 2323, 80]), 'TCP', 60, 'SYN',
                                             source='real')
                        time.sleep(0.05)
                elif at < 0.6:
                    for _ in range(random.randint(5, 15)):
                        self._process_packet(src, target, random.randint(30000, 40000),
                                             random.choice([80, 443]), 'UDP', 1400,
                                             source='real')
                        time.sleep(0.03)
                elif at < 0.8:
                    for _ in range(random.randint(2, 4)):
                        self._process_packet(src, target, random.randint(50000, 60000),
                                             22, 'TCP', 80, 'SYN', source='real')
                        time.sleep(0.1)
                else:
                    self._process_packet(src, random.choice(attackers), 52341,
                                         46370, 'TCP', 200, 'PSH', source='real')
            else:
                dev = random.choice(device_ips)
                nt = random.random()
                if nt < 0.4:
                    # MQTT 遥测到网关
                    self._process_packet(dev, hub, random.randint(40000, 50000),
                                         1883, 'TCP', random.randint(60, 200), 'PA',
                                         source='real')
                elif nt < 0.6:
                    # 设备间 CoAP 联动
                    peer = random.choice(device_ips)
                    if peer != dev:
                        self._process_packet(dev, peer, random.randint(40000, 50000),
                                             5683, 'UDP', random.randint(60, 200), '',
                                             source='real')
                elif nt < 0.8:
                    # 外网 HTTP/DNS
                    self._process_packet(dev, cloud, random.randint(40000, 50000),
                                         random.choice([443, 53]), 'TCP',
                                         random.randint(200, 1500), 'A', source='real')
                else:
                    # 普通 DNS/NTP
                    self._process_packet(dev, hub, random.randint(40000, 50000),
                                         random.choice([53, 123]), 'UDP',
                                         random.randint(60, 500), '', source='real',
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
        for f in flows:
            self.device_detector.add_flow(f)
        result = self.device_detector.detect_window()
        if result:
            self.device_risk_cache = result
            summary = self._persist_device_risk(result)
            if summary:
                self.alert_count += summary.get("incidents_created", 0)
        return result

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
