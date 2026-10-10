"""本机合成验证：6 台物理设备固件形态流量 -> 现役 ONNX GNN 输出。

用途（真实路线可行性预检）：不烧板子、不用 Pi，直接按固件真实行为建模
一个 300 秒检测窗口的流量，喂给部署模型，回答两个问题：
  1. 正常设备（现固件 / LINK_SIM_MODE 固件）会被判绿还是被域偏移误报？
  2. 攻击设备（现固件 UDP 9000 洪水 / Mirai 化攻击）会被判红吗？强度要多高？

场景（每场一个独立 300s 窗口）：
  1. 现固件正常流量：6 设备 5s MQTT + 30s 无条件 CoAP 联动
  2. 场景1 + 现固件攻击（door-01 -> .200:9000 UDP 10pkt/s × 30s = 300 包）
  3. LINK_SIM_MODE 正常流量：MQTT + DNS/NTP + camera HTTP + 角色托管 CoAP
  4. 场景3 + Mirai 化攻击（内网 SYN 扫描 23/2323/80 + 外网 UDP 洪水）
  5. 攻击强度扫描（场景3 基础上，洪水包数 300 -> 30000）
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.device_detector import DeviceGraphDetector  # noqa: E402

HUB = '192.168.4.1'
CAM, DOOR, LIGHT, PLUG, SENSOR, SPEAKER = (
    '192.168.4.10', '192.168.4.11', '192.168.4.12',
    '192.168.4.13', '192.168.4.14', '192.168.4.15',
)
DEVS = [CAM, DOOR, LIGHT, PLUG, SENSOR, SPEAKER]
VICTIM = '192.168.4.200'
SCAN_PORTS = [23, 2323, 80]
EXTERNAL = ['8.8.8.8', '1.1.1.1', '9.9.9.9']
WINDOW = 300.0

_rng = np.random.default_rng(42)


def _mqtt(dev):
    """持久 MQTT 连接：5s 一次 publish，300s 窗口 60 次，每次约 4 包（段+ACK）。"""
    sport = 40000 + int(dev.split('.')[-1])
    return [{
        'src_ip': dev, 'dst_ip': HUB, 'src_port': sport, 'dst_port': 1883,
        'protocol': 6, 'packets': 60 * 4, 'bytes': int(60 * _rng.integers(200, 640)),
        'tcp_flags': 24, 'ts_start': 0.0, 'ts_end': WINDOW - 1,
    }]


def _coap(dev, targets, per=10, size=31):
    """CoAP 联动：持久 UDP socket 固定源端口，'evt' 3 字节 + 头开销。"""
    flows = []
    sport = 50000 + int(dev.split('.')[-1])
    for tgt in targets:
        flows.append({
            'src_ip': dev, 'dst_ip': tgt, 'src_port': sport, 'dst_port': 5683,
            'protocol': 17, 'packets': per, 'bytes': per * size,
            'tcp_flags': 0, 'ts_start': 0.0, 'ts_end': WINDOW - 1,
        })
    return flows


def _dns_ntp(dev):
    """LINK_SIM 背景流量：外网 DNS/NTP 查询。"""
    sport = 60000 + int(dev.split('.')[-1])
    return [
        {'src_ip': dev, 'dst_ip': '8.8.8.8', 'src_port': sport, 'dst_port': 53,
         'protocol': 17, 'packets': 5, 'bytes': 5 * 60, 'tcp_flags': 0,
         'ts_start': 0.0, 'ts_end': 2.0},
        {'src_ip': dev, 'dst_ip': '129.6.15.28', 'src_port': sport + 100, 'dst_port': 123,
         'protocol': 17, 'packets': 3, 'bytes': 3 * 76, 'tcp_flags': 0,
         'ts_start': 0.0, 'ts_end': 2.0},
    ]


def _camera_http():
    """camera -> 8.8.8.8:80 周期出向（视频边流量证据，20~40s 周期）。"""
    return [{
        'src_ip': CAM, 'dst_ip': '8.8.8.8', 'src_port': 47000, 'dst_port': 80,
        'protocol': 6, 'packets': 10 * 8, 'bytes': 10 * 1500, 'tcp_flags': 24,
        'ts_start': 0.0, 'ts_end': 1.0,
    }]


def scenario_current_fw():
    """现固件正常流量：MQTT + 无条件 30s CoAP。"""
    flows = [_mqtt(d) for d in DEVS]
    flows = [f for group in flows for f in group]
    flows += _coap(DOOR, [SPEAKER, CAM])   # 开门 -> 音箱/摄像头（30s 无条件）
    flows += _coap(SENSOR, [PLUG])         # 温度 -> 插座（30s 无条件）
    return flows


def attack_udp9000(flows, start=240.0):
    """现固件攻击：-> .200:9000 UDP 4B × 10pkt/s × 30s。"""
    return flows + [{
        'src_ip': DOOR, 'dst_ip': VICTIM, 'src_port': 55555, 'dst_port': 9000,
        'protocol': 17, 'packets': 300, 'bytes': 300 * 32,
        'tcp_flags': 0, 'ts_start': start, 'ts_end': start + 30,
    }]


def scenario_link_sim_fw():
    """LINK_SIM_MODE 正常流量：MQTT + DNS/NTP + camera HTTP + 角色托管 CoAP。"""
    flows = [f for d in DEVS for f in _mqtt(d)]
    flows += [f for d in DEVS for f in _dns_ntp(d)]
    flows += _camera_http()
    flows += _coap(DOOR, [SPEAKER, CAM])                # 真实角色
    flows += _coap(SENSOR, [PLUG])                      # 真实角色
    flows += _coap(SPEAKER, [LIGHT])                    # speaker 托管门磁 -> 楼道灯
    flows += _coap(SENSOR, [DOOR, LIGHT, PLUG], per=6)  # sensor 托管烟雾/温湿度
    return flows


def attack_mirai(flows, start=240.0, flood_pkts=600):
    """Mirai 化攻击：内网 SYN 扫描 23/2323/80 + 外网 UDP 洪水 512~1400B。"""
    out = list(flows)
    for victim in [CAM, LIGHT, SENSOR, SPEAKER]:
        for port in SCAN_PORTS:
            out.append({
                'src_ip': DOOR, 'dst_ip': victim,
                'src_port': int(_rng.integers(50000, 60000)), 'dst_port': port,
                'protocol': 6, 'packets': 3, 'bytes': 3 * 60, 'tcp_flags': 2,
                'ts_start': start, 'ts_end': start + 5,
            })
    for ext in EXTERNAL:
        out.append({
            'src_ip': DOOR, 'dst_ip': ext,
            'src_port': int(_rng.integers(30000, 40000)),
            'dst_port': int(_rng.integers(1024, 65535)), 'protocol': 17,
            'packets': flood_pkts // 3, 'bytes': (flood_pkts // 3) * 1000,
            'tcp_flags': 0, 'ts_start': start, 'ts_end': start + 30,
        })
    return out


def run(name, flows):
    det = DeviceGraphDetector(window_seconds=WINDOW, community_subnet='192.168.4.')
    for f in flows:
        det.add_flow(f)
    result = det.detect_window()
    print(f"=== {name} ===")
    if not result:
        print("  (无结果)")
        return result
    for ip in sorted(result):
        r = result[ip]
        probs = ', '.join(f'{p:.2f}' for p in r['probs'])
        print(f"  {ip:16s} -> {r['name']:<6s} level={r['level']}  probs=[{probs}]")
    return result


if __name__ == '__main__':
    run('场景1 现固件正常流量（6设备 300s）', scenario_current_fw())
    run('场景2 场景1 + 现固件攻击（UDP 9000 洪水 300包）',
        attack_udp9000(scenario_current_fw()))
    run('场景3 LINK_SIM 正常流量（MQTT+DNS/NTP+HTTP+托管CoAP）',
        scenario_link_sim_fw())
    run('场景4 场景3 + Mirai化攻击（SYN扫描+外网洪水 600包）',
        attack_mirai(scenario_link_sim_fw()))
    print()
    print("=== 场景5 攻击强度扫描（LINK_SIM 场景 + 递增洪水包数）===")
    for pkts in [300, 1000, 3000, 10000, 30000]:
        flows = attack_mirai(scenario_link_sim_fw(), flood_pkts=pkts)
        det = DeviceGraphDetector(window_seconds=WINDOW, community_subnet='192.168.4.')
        for f in flows:
            det.add_flow(f)
        result = det.detect_window()
        door = result.get(DOOR, {})
        print(f"  洪水 {pkts:>6} 包 -> {door.get('name', '缺'):<8s} "
              f"level={door.get('level', '-')} "
              f"probs={[f'{p:.2f}' for p in door.get('probs', [])]}")
        others = {ip: r['level'] for ip, r in result.items()
                  if ip != DOOR and ip != HUB and ip != VICTIM}
        print(f"        其余设备: {others}")
    print()
    print("=== 场景6 纯洪水攻击（无内网扫描）强度扫描 ===")
    _rng = np.random.default_rng(7)
    for pkts in [300, 1000, 3000, 10000]:
        flows = list(scenario_link_sim_fw())
        for ext in ['8.8.8.8', '1.1.1.1', '9.9.9.9']:
            flows.append({'src_ip': DOOR, 'dst_ip': ext,
                          'src_port': int(_rng.integers(30000, 40000)),
                          'dst_port': int(_rng.integers(1024, 65535)), 'protocol': 17,
                          'packets': pkts // 3, 'bytes': (pkts // 3) * 1000,
                          'tcp_flags': 0, 'ts_start': 240.0, 'ts_end': 270.0})
        det = DeviceGraphDetector(window_seconds=WINDOW, community_subnet='192.168.4.')
        for f in flows:
            det.add_flow(f)
        result = det.detect_window()
        door = result.get(DOOR, {})
        others = {ip: r['level'] for ip, r in result.items()
                  if ip not in (DOOR, HUB, VICTIM)}
        print(f"  洪水 {pkts:>6} 包 -> 攻击者: {door.get('name', '缺')} lv={door.get('level', '-')} "
              f"| 其余: {others}")
