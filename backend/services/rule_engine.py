"""
Mirai 规则签名引擎 —— 仿真模式的确定性检测层（生产 NDR 第一层：签名/规则）。

生产 NDR（Suricata/Vectra）的第一层是规则签名，对已知攻击零域偏移、零训练。
本模块为仿真模式提供与 ``traffic_capture._capture_simulate`` 攻击分支**单一来源**
的 Mirai 签名常量，以及按源 IP 统计的每检测窗口计数器：

  - 内网端口扫描：SYN 到 {23, 2323, 80}（Mirai 扫 telnet/HTTP）
  - 外网 UDP 洪水：到外网目标的 UDP（排除 DNS/NTP 正常服务端口）
  - C2 通信：到固定 C2 服务器的持久 TCP
  - 受害机洪水：对隔离网段受害机 .200:9000 的 UDP 洪水（现场演示主签名）

GNN 的橙/红判定在仿真模式会被规则层否决（无签名 → 降绿），解决域偏移误报；
真实抓包模式下规则层只计数、不否决（fusion 仅在 capture_mode=='sim' 启用）。

现场演示（真实抓包）下，udp_flood/syn_scan 对正常设备（mDNS/QUIC/私有云上行）
噪声较大，可通过 IOT_IDS_RULE_SIGNATURES=victim_flood 只用精确的受害机洪水签名。
"""
import os
import threading

# ---- 与 traffic_capture._capture_simulate 攻击分支共享的签名常量（单一来源） ----
SCAN_PORTS = (23, 2323, 80)       # Mirai 扫描端口（telnet/HTTP）
C2_SERVER = '10.99.1.100'         # 僵尸网络 C2 服务器
C2_PORT = 443                     # C2 通信端口
NORMAL_UDP_PORTS = (53, 123)      # 外网 UDP 的正常服务端口（DNS/NTP，不算洪水）

# 现场演示攻击目标（与固件 device_secrets.h 的 IOT_LAB_ATTACK_TARGET/PORT 一致）
VICTIM_IP = '192.168.4.200'
VICTIM_PORT = 9000

# 判定阈值（每个检测窗口内的最小命中数；攻击分支每窗口命中量远超阈值）
SYN_SCAN_MIN_HITS = 3
UDP_FLOOD_MIN_PKTS = 10
C2_MIN_HITS = 1
VICTIM_FLOOD_MIN_PKTS = 5        # 对受害机 .200:9000 的 UDP 洪水（现场演示主签名）

# 启用哪些签名：'all'（默认，仿真模式）= 全部；'victim_flood'（现场演示）= 只用受害机洪水
_RULE_SIGNATURES = os.environ.get('IOT_IDS_RULE_SIGNATURES', 'all').strip().lower()


class DeviceRuleCounter:
    """单台设备在一个检测窗口内的 Mirai 签名计数。"""

    __slots__ = ('syn_scan_hits', 'udp_flood_pkts', 'c2_hits', 'victim_flood_pkts')

    def __init__(self):
        self.syn_scan_hits = 0
        self.udp_flood_pkts = 0
        self.c2_hits = 0
        self.victim_flood_pkts = 0

    def has_mirai_signature(self) -> bool:
        """是否命中 Mirai 签名（任一特征达到阈值）。"""
        if _RULE_SIGNATURES == 'victim_flood':
            return self.victim_flood_pkts >= VICTIM_FLOOD_MIN_PKTS
        return (
            self.syn_scan_hits >= SYN_SCAN_MIN_HITS
            or self.udp_flood_pkts >= UDP_FLOOD_MIN_PKTS
            or self.c2_hits >= C2_MIN_HITS
            or self.victim_flood_pkts >= VICTIM_FLOOD_MIN_PKTS
        )


class RuleEngine:
    """按源 IP 维护每检测窗口的 Mirai 签名计数器。"""

    def __init__(self, community_subnet: str = '192.168.4.'):
        self._community_subnet = community_subnet
        self._counters = {}
        self._lock = threading.Lock()

    def on_packet(self, src_ip, dst_ip, src_port, dst_port, protocol, flags):
        """按包更新源 IP 的签名计数（只统计设备的**出向**攻击特征）。"""
        dst_external = not str(dst_ip).startswith(self._community_subnet)
        with self._lock:
            counter = self._counters.get(src_ip)
            if counter is None:
                counter = DeviceRuleCounter()
                self._counters[src_ip] = counter
            if flags == 'SYN' and dst_port in SCAN_PORTS and not dst_external:
                counter.syn_scan_hits += 1
            if protocol == 'UDP' and dst_external and dst_port not in NORMAL_UDP_PORTS:
                counter.udp_flood_pkts += 1
            if dst_ip == C2_SERVER and dst_port == C2_PORT:
                counter.c2_hits += 1
            if protocol == 'UDP' and dst_ip == VICTIM_IP and dst_port == VICTIM_PORT:
                counter.victim_flood_pkts += 1

    def snapshot_and_reset(self):
        """取出当前计数器的快照并清空（与流量聚合器的 flush 同步调用）。"""
        with self._lock:
            snapshot = dict(self._counters)
            self._counters = {}
            return snapshot
