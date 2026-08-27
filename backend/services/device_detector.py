"""
设备图 GNN 实时检测引擎 — 时间窗口式设备级四级风险分类。

流程:
    数据包 -> 聚合为流(5元组) -> 累积一个时间窗口
           -> 建设备图(13维特征+邻接) -> ONNX 推理 -> 每台设备风险等级

与现有流级 GAT(gat_detector.py)互补: GAT 判断"哪条流是攻击",
本模块判断"哪台设备被感染"(绿/黄/橙/红)。树莓派边缘共用同一代码路径。

用法:
    det = DeviceGraphDetector(community_subnet='192.168.4.')
    det.add_flow({'src_ip': ..., 'dst_ip': ..., ...})
    result = det.detect_window()   # {device_ip: {'level', 'name', 'probs'}}
"""
import os
import time
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from models.device_gnn_inference import DeviceGNNInference

# 协议字符串 -> IP 协议号（与训练数据 protocol 列一致）
PROTO_MAP = {'TCP': 6, 'UDP': 17, 'ICMP': 1}

# TCP flags 字符串 -> 位掩码（与训练数据 tcp_flags 一致）
FLAG_BITS = {'F': 1, 'S': 2, 'R': 4, 'P': 8, 'A': 16, 'U': 32, 'E': 64, 'C': 128}


def encode_flags(flags_str: str) -> int:
    """'SA' -> 18, 'PA' -> 24, 'S' -> 2 ..."""
    val = 0
    for ch in flags_str or '':
        val |= FLAG_BITS.get(ch.upper(), 0)
    return val


class FlowAggregator:
    """数据包 -> 流(5元组聚合)，输出设备 GNN 需要的流字段。"""

    def __init__(self):
        self._flows: Dict[tuple, dict] = {}

    def add_packet(self, src_ip, dst_ip, src_port, dst_port, protocol,
                   length, flags='', ts: Optional[float] = None):
        """累积一个数据包。protocol 为 'TCP'/'UDP'/'ICMP' 字符串。"""
        proto_num = PROTO_MAP.get(protocol, 0)
        key = (src_ip, dst_ip, src_port, dst_port, proto_num)
        ts = ts if ts is not None else time.time()
        if key not in self._flows:
            self._flows[key] = {
                'src_ip': src_ip, 'dst_ip': dst_ip,
                'src_port': src_port, 'dst_port': dst_port,
                'protocol': proto_num,
                'packets': 0, 'bytes': 0, 'tcp_flags': 0,
                'ts_start': ts, 'ts_end': ts,
            }
        f = self._flows[key]
        f['packets'] += 1
        f['bytes'] += length
        f['tcp_flags'] |= encode_flags(flags)
        f['ts_start'] = min(f['ts_start'], ts)
        f['ts_end'] = max(f['ts_end'], ts)

    def flush(self) -> List[dict]:
        """取出所有流并清空。"""
        out = list(self._flows.values())
        self._flows.clear()
        return out


class DeviceGraphDetector:
    """时间窗口式设备级检测引擎。"""

    def __init__(self, window_seconds: float = 60.0,
                 community_subnet: str = '192.168.4.',
                 onnx_path: Optional[str] = None,
                 norm_path: Optional[str] = None):
        self.window_seconds = window_seconds
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        onnx_path = onnx_path or os.path.join(here, 'data', 'device_gnn.onnx')
        norm_path = norm_path or os.path.join(here, 'data', 'device_gnn_norm.npz')
        self.inference = DeviceGNNInference(onnx_path, norm_path, community_subnet)
        self._flow_buffer: List[dict] = []
        self._window_start = time.time()

    @property
    def model_loaded(self) -> bool:
        return self.inference.session is not None

    def add_flow(self, flow: dict):
        """累积一条流。flow 需含 src_ip/dst_ip/src_port/dst_port/protocol/packets/bytes/tcp_flags/ts_start/ts_end。"""
        self._flow_buffer.append(flow)

    def should_detect(self) -> bool:
        """当前窗口是否已满。"""
        return time.time() - self._window_start >= self.window_seconds

    def detect_window(self) -> Dict[str, dict]:
        """对当前累积的流建设备图并推理，返回 {设备IP: {level, name, probs}}。"""
        if not self._flow_buffer:
            return {}
        flows_df = pd.DataFrame(self._flow_buffer)
        result = self.inference.predict(flows_df)
        self._flow_buffer.clear()
        self._window_start = time.time()
        return result


if __name__ == '__main__':
    # 端到端冒烟测试：模拟 3 台正常设备 + 1 台被感染设备
    det = DeviceGraphDetector(window_seconds=1.0)
    print(f'模型加载: {det.model_loaded}\n')

    rng = np.random.default_rng(42)
    hub = '192.168.4.1'
    ext = '8.8.8.8'
    t = time.time()

    # 正常设备：MQTT 遥测（小包 TCP，到网关）
    for dev in ['192.168.4.10', '192.168.4.12', '192.168.4.14']:
        for _ in range(20):
            det.add_flow({
                'src_ip': dev, 'dst_ip': hub, 'src_port': int(rng.integers(40000, 50000)),
                'dst_port': 1883, 'protocol': 6,
                'packets': 6, 'bytes': int(rng.integers(60, 200)), 'tcp_flags': 24,
                'ts_start': t, 'ts_end': t + 0.05,
            })

    # 被感染设备：Mirai UDP 洪水（高包量 UDP，到外部）
    for _ in range(300):
        det.add_flow({
            'src_ip': '192.168.4.11', 'dst_ip': ext,
            'src_port': int(rng.integers(30000, 60000)), 'dst_port': int(rng.integers(1024, 65535)),
            'protocol': 17, 'packets': 50, 'bytes': 1400, 'tcp_flags': 0,
            'ts_start': t, 'ts_end': t + 0.01,
        })

    result = det.detect_window()
    print('=== 设备风险检测结果 ===')
    if not result:
        print('  (无结果)')
    for ip in sorted(result):
        r = result[ip]
        print(f"  {ip:16s} -> {r['name']:<8s} 置信度 {r['probs'].max():.1%}")
