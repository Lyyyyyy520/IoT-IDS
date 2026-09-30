"""
树莓派边缘检测程序 — 设备图 GNN 本地推理 + 边云协同上报。

边云协同分工：
  边缘端(树莓派)：
    1. 本地抓包(tcpdump)
    2. 聚合为流(FlowAggregator)
    3. 按窗口建设备图 + ONNX 本地推理(DeviceGraphDetector)
    4. 只把轻量的「设备风险结果」上报云端 POST /api/device/risk
    5. 本地终端打印风险等级
  云端(后端)：
    接收风险结果 → 写 v3_device_current_state → 高危生成 v3 incident → 前端可视化

原始流量不出边缘，只上行每台设备的风险等级与置信度，符合「边缘就近推理、云端协调展示」。

用法:
  python edge_detect.py --demo                                  # 模拟流量冒烟测试(不联网)
  python edge_detect.py --live --server http://<cloud>:5000 --probe-token <token>
  python edge_detect.py --live --interface wlan0                # 只本地打印，不上报
"""
import os
import sys
import time
import argparse
import re
import json
import subprocess
from datetime import datetime, timezone
from uuid import uuid4

import numpy as np

# 复用后端同一套推理代码（纯 numpy/pandas/onnxruntime，无 Flask 依赖）
_here = os.path.dirname(os.path.abspath(__file__))
for _p in (
    os.path.join(_here, '..', 'backend'),
    os.path.join(_here, 'backend'),
    '/home/pi/iot-ids/backend',
):
    sys.path.insert(0, _p)

from services.device_detector import FlowAggregator, DeviceGraphDetector  # noqa: E402

COMMUNITY_SUBNET = '192.168.4.'
WINDOW_SECONDS = 60.0

RISK_ICON = {0: '绿', 1: '黄', 2: '橙', 3: '红'}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def detect_demo() -> None:
    """用模拟流量跑一次设备级四级风险分类（离线冒烟测试）。"""
    det = DeviceGraphDetector(window_seconds=1.0, community_subnet=COMMUNITY_SUBNET)
    print('=' * 60)
    print('  IoT IDS 边缘检测 — 设备图 GNN 演示模式')
    print('=' * 60)
    print(f'  模型加载: {det.model_loaded}\n')

    if not det.model_loaded:
        print('[!] 模型未加载：请确认 backend/data/device_gnn_hetero.onnx 存在')
        return

    rng = np.random.default_rng(42)
    hub = '192.168.4.1'
    ext = '192.168.4.200'
    t = time.time()

    # 3 台正常设备：MQTT 遥测（小包 TCP 到网关）
    for dev in ['192.168.4.10', '192.168.4.12', '192.168.4.14']:
        for _ in range(20):
            det.add_flow({
                'src_ip': dev, 'dst_ip': hub,
                'src_port': int(rng.integers(40000, 50000)), 'dst_port': 1883,
                'protocol': 6, 'packets': 6,
                'bytes': int(rng.integers(60, 200)), 'tcp_flags': 24,
                'ts_start': t, 'ts_end': t + 0.05,
            })

    # 1 台被感染设备：Mirai UDP 洪水（高包量 UDP 到外部）
    for _ in range(300):
        det.add_flow({
            'src_ip': '192.168.4.11', 'dst_ip': ext,
            'src_port': int(rng.integers(30000, 60000)),
            'dst_port': int(rng.integers(1024, 65535)),
            'protocol': 17, 'packets': 50, 'bytes': 1400, 'tcp_flags': 0,
            'ts_start': t, 'ts_end': t + 0.01,
        })

    result = det.detect_window()
    print('=== 设备风险检测结果 ===')
    if not result:
        print('  (无结果)')
    for ip in sorted(result):
        r = result[ip]
        icon = RISK_ICON.get(r['level'], '?')
        print(f"  {ip:16s} -> {icon} {r['name']:<8s} 置信度 {max(r['probs']):.1%}")


def _push_risk(server_url: str, probe_token: str, risks: dict,
               source_id: str, source_session_id: str, batch_sequence: int) -> bool:
    """把设备风险结果上报云端（轻量，只含风险等级与置信度，不上行原始流量）。"""
    import requests
    payload = {
        'source_id': source_id,
        'source_session_id': source_session_id,
        'batch_sequence': batch_sequence,
        'occurred_at': _now(),
        'devices': [
            {
                'ip': ip,
                'level': r['level'],
                'name': r['name'],
                'confidence': float(max(r['probs'])),
            }
            for ip, r in risks.items()
        ],
    }
    try:
        resp = requests.post(
            f'{server_url}/api/device/risk',
            headers={'X-Probe-Token': probe_token},
            json=payload,
            timeout=5,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # 上报失败不影响边缘本地检测
        print('  [上报失败]', type(exc).__name__)
        return False


def _extract_flags(line: str) -> str:
    """从 tcpdump 行提取 TCP flags 字符串，如 '[S.]' -> 'S'、'[P.]' -> 'P'。"""
    m = re.search(r'Flags \[([^\]]*)\]', line)
    if not m:
        return ''
    return m.group(1).replace('.', '').replace(',', '')


def detect_live(server_url=None, probe_token='', interface='wlan0') -> None:
    """本地抓包 → 流聚合 → 按窗口建设备图推理 → 上报风险结果。"""
    aggregator = FlowAggregator()
    detector = DeviceGraphDetector(
        window_seconds=WINDOW_SECONDS, community_subnet=COMMUNITY_SUBNET)

    print('=' * 60)
    print('  IoT IDS 边缘检测 — 设备图 GNN 实时模式')
    print('=' * 60)
    print(f'  模型加载: {detector.model_loaded}')
    print(f'  抓包接口: {interface}')
    print(f'  上报目标: {server_url or "(仅本地打印)"}\n')

    if not detector.model_loaded:
        print('[!] 模型未加载，退出。')
        return

    source_session_id = str(uuid4())
    batch_sequence = 0

    cmd = ['sudo', 'tcpdump', '-i', interface, '-l', '-n', '-tt', 'ip']
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=0)
    ip_re = re.compile(r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s*>\s*(\d+\.\d+\.\d+\.\d+)\.(\d+)')
    length_re = re.compile(r'\blength\s+(\d+)')

    window_start = time.time()
    print('抓包开始。Ctrl+C 停止。\n')
    try:
        for line in iter(proc.stdout.readline, ''):
            match = ip_re.search(line)
            if not match:
                continue
            src_ip, sport = match.group(1), int(match.group(2))
            dst_ip, dport = match.group(3), int(match.group(4))
            upper = line.upper()
            if 'TCP' in upper:
                protocol = 'TCP'
            elif 'UDP' in upper:
                protocol = 'UDP'
            elif 'ICMP' in upper:
                protocol = 'ICMP'
            else:
                continue
            length_m = length_re.search(line)
            length = int(length_m.group(1)) if length_m else 0

            aggregator.add_packet(
                src_ip, dst_ip, sport, dport, protocol, length,
                flags=_extract_flags(line))

            # 窗口满：冲刷流 → 建设备图推理 → 上报
            if time.time() - window_start >= WINDOW_SECONDS:
                flows = aggregator.flush()
                for f in flows:
                    detector.add_flow(f)
                risks = detector.detect_window()
                if risks:
                    batch_sequence += 1
                    print(f'[{_now()}] 窗口检测 {len(risks)} 台设备:')
                    for ip in sorted(risks):
                        r = risks[ip]
                        icon = RISK_ICON.get(r['level'], '?')
                        print(f"    {ip:16s} -> {icon} {r['name']:<8s} "
                              f"置信度 {max(r['probs']):.1%}")
                    if server_url:
                        _push_risk(server_url, probe_token, risks,
                                   'edge:pi-gateway', source_session_id, batch_sequence)
                window_start = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        print('\n抓包停止。')


def main() -> None:
    parser = argparse.ArgumentParser(description='IoT IDS 边缘设备图 GNN 检测（边云协同）')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true', help='实时抓包模式')
    mode.add_argument('--demo', action='store_true', help='模拟流量演示模式（默认）')
    parser.add_argument('--server', default=os.getenv('IOT_IDS_SERVER_URL', ''),
                        help='云端后端 URL（默认读环境变量 IOT_IDS_SERVER_URL）')
    parser.add_argument('--probe-token', default=os.getenv('IOT_IDS_PROBE_TOKEN', ''),
                        help='probe 凭据（默认读环境变量 IOT_IDS_PROBE_TOKEN）')
    parser.add_argument('--interface', default='wlan0', help='抓包接口（默认 wlan0）')
    args = parser.parse_args()

    if args.live:
        if args.server and not args.probe_token:
            raise SystemExit('缺少 probe 凭据：设置 IOT_IDS_PROBE_TOKEN 或传 --probe-token')
        detect_live(args.server, args.probe_token, args.interface)
    else:
        detect_demo()


if __name__ == '__main__':
    main()
