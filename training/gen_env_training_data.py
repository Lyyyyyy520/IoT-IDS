"""环境形态标注训练数据生成器 —— 部署域适配训练集（docs/11 阶段5）。

背景：现役模型在 6 台固件形态流量上整体失效（正常判橙/红、攻击判黄）。
根因之一：训练集（CICIoT2023）里没有三类部署必现的输入——
  1. 网关节点（有出向 PUBACK 流、node_type=0）
  2. 纯接收节点（受害机等，零出向特征）
  3. 固件真实形态的稀疏正常流量（5s MQTT + CoAP + DNS/NTP/HTTP）

本生成器按固件真实行为（community_device.ino / camera_device.ino / LINK_SIM_MODE
方案）生成标注流量，与 CICIoT2023 混合后重训。列格式与 flows_sample.csv 完全一致。

用法: python training/gen_env_training_data.py [输出CSV路径]
"""
import random
import sys

import numpy as np
import pandas as pd

SEED = 42
random.seed(SEED)
np.random.seed(SEED)

GW = '192.168.137.1'
# 6 台物理设备：IP 尾号 -> 角色
CORE_ROLES = {10: 'camera', 11: 'door', 12: 'light', 13: 'plug', 14: 'sensor', 15: 'speaker'}
CORE = [f'192.168.137.{i}' for i in sorted(CORE_ROLES)]
VICTIMS = ['192.168.137.200', '192.168.137.201']
DNS_SERVERS = ['8.8.8.8', '1.1.1.1', '9.9.9.9']
NTP_SERVERS = ['129.6.15.28', '216.239.35.0']
CLOUDS = ['10.0.0.1', '10.0.0.2', '10.0.0.3', '10.0.0.4', '10.0.0.5']
C2_SERVER = '10.99.1.100'
EXT_FLOOD = ['45.33.32.156', '8.8.8.8', '1.1.1.1']
SCAN_PORTS = [23, 2323, 80]
WINDOW = 60.0
T0 = 1700000000.0  # 独立时间段，避免与 CICIoT 窗口索引冲突

# 标签（与 train_htgat_sparse.py LABEL_TO_LEVEL 一致）
L_BENIGN = 'Benign_Final'
L_RECON = 'Recon-PortScan'
L_DOS = 'DoS-TCP_Flood'
L_MIRAI_UDP = 'Mirai-udpplain'     # 现固件形态：内网单目标 UDP 洪水
L_MIRAI_FULL = 'Mirai-greip_flood'  # 完整形态：SYN 扫描 + 外网洪水 + C2


def _port(ip, base):
    return base + int(ip.split('.')[-1])


class WindowGen:
    """单窗口流量生成器。"""

    def __init__(self, win_idx, devices, extended=False):
        self.t0 = T0 + win_idx * WINDOW
        self.devices = devices
        self.rows = []
        self.gateway_pubacks = []  # (dev, sport, packets, bytes)

    def benign_device(self, dev, role, minimal=False):
        """固件形态正常流量（含 LINK_SIM 丰富变体；minimal=现固件最简形态）。"""
        t0, rows = self.t0, self.rows
        octet = int(dev.split('.')[-1])
        rnd = random.random()

        # MQTT 遥测：持久 TCP 连接，3~7s 一次 publish
        mqtt_sport = 40000 + octet
        publishes = int(np.random.randint(8, 20))
        avg_pub = float(np.random.uniform(200, 640))
        rows.append(dict(src_ip=dev, dst_ip=GW, src_port=mqtt_sport, dst_port=1883,
                         protocol=6, packets=int(publishes * np.random.uniform(3, 6)),
                         bytes=int(publishes * avg_pub), tcp_flags=24,
                         ts_start=t0 + 1, ts_end=t0 + WINDOW - 4, label_class=L_BENIGN))
        self.gateway_pubacks.append((dev, mqtt_sport, int(publishes * 3), int(publishes * 200)))

        # CoAP 联动（固件语义：door->speaker/camera、sensor->plug；LINK_SIM 变体含角色托管）
        coap_sport = 50000 + octet
        targets = []
        if role == 'door':
            targets = [f'192.168.137.{15}', f'192.168.137.{10}']
        elif role == 'sensor':
            targets = [f'192.168.137.{13}']
            if rnd > 0.5:  # LINK_SIM：传感器托管烟雾/温湿度 -> 门禁/灯/插座
                targets = [f'192.168.137.{11}', f'192.168.137.{12}', f'192.168.137.{13}']
        elif role == 'speaker' and rnd > 0.5:  # LINK_SIM：音箱托管门磁 -> 楼道灯
            targets = [f'192.168.137.{12}']
        elif role == 'camera' and rnd > 0.7:
            targets = [f'192.168.137.{11}']
        for tgt in targets:
            n = int(np.random.randint(1, 4))
            rows.append(dict(src_ip=dev, dst_ip=tgt, src_port=coap_sport, dst_port=5683,
                             protocol=17, packets=n, bytes=n * int(np.random.uniform(28, 60)),
                             tcp_flags=0, ts_start=t0 + 5, ts_end=t0 + WINDOW - 10,
                             label_class=L_BENIGN))

        # 随机 benign「非常规端口」流：教模型"非标准端口边 ≠ 可疑"
        # （CICIoT 的 benign 含大量随机端口；固件 UDP socket 源端口也是随机的）
        if not minimal and rnd > 0.55:
            n = int(np.random.randint(1, 4))
            odd_port = int(np.random.choice([8080, 9000, 10000, 34567, 47808]))
            rows.append(dict(src_ip=dev, dst_ip=random.choice(self.devices),
                             src_port=int(np.random.randint(60000, 65000)),
                             dst_port=odd_port, protocol=17, packets=n, bytes=n * 40,
                             tcp_flags=0, ts_start=t0 + 25, ts_end=t0 + 25 + 2,
                             label_class=L_BENIGN))

        if minimal:
            return  # 现固件最简形态：只有 MQTT + CoAP

        # DNS / NTP（LINK_SIM 背景流量，部分设备带）
        if rnd > 0.35:
            dns_sport = 60000 + octet
            n = int(np.random.randint(1, 2))
            rows.append(dict(src_ip=dev, dst_ip=random.choice(DNS_SERVERS), src_port=dns_sport,
                             dst_port=53, protocol=17, packets=n * 2, bytes=n * 120,
                             tcp_flags=0, ts_start=t0 + 30, ts_end=t0 + 30 + 5, label_class=L_BENIGN))
            ntp_sport = 61000 + octet
            n = 1
            rows.append(dict(src_ip=dev, dst_ip=random.choice(NTP_SERVERS), src_port=ntp_sport,
                             dst_port=123, protocol=17, packets=n, bytes=n * 76,
                             tcp_flags=0, ts_start=t0 + 25, ts_end=t0 + 25 + 5, label_class=L_BENIGN))

        # HTTP 出向（camera 视频边证据；其它设备偶尔云 API）
        if role == 'camera' or rnd > 0.7:
            http_sport = 47000 + octet
            n = int(np.random.randint(1, 4))
            rows.append(dict(src_ip=dev, dst_ip=random.choice(CLOUDS),
                             src_port=http_sport, dst_port=random.choice([80, 443]),
                             protocol=6, packets=n * int(np.random.randint(6, 14)),
                             bytes=n * int(np.random.uniform(400, 1500)), tcp_flags=24,
                             ts_start=t0 + 25, ts_end=t0 + 25 + 30, label_class=L_BENIGN))

    def gateway(self):
        """网关出向 PUBACK（node_type=0 节点获得真实特征，label=0）。"""
        for dev, sport, packets, bytes_ in self.gateway_pubacks:
            self.rows.append(dict(src_ip=GW, dst_ip=dev, src_port=1883, dst_port=sport,
                                  protocol=6, packets=packets, bytes=bytes_, tcp_flags=16,
                                  ts_start=self.t0 + 1, ts_end=self.t0 + WINDOW - 4,
                                  label_class=L_BENIGN))

    def attack_firmware(self, attacker, victim):
        """现固件攻击形态：内网 UDP 9000 洪水（10pkt/s × 30~90s）。"""
        n = int(np.random.choice([300, 600, 900]))
        self.rows.append(dict(src_ip=attacker, dst_ip=victim, src_port=55555, dst_port=9000,
                              protocol=17, packets=n, bytes=n * 32, tcp_flags=0,
                              ts_start=self.t0 + 20, ts_end=self.t0 + 20 + 30,
                              label_class=L_MIRAI_UDP))

    def attack_mirai(self, attacker, victims, intensity='med'):
        """完整 Mirai 形态：内网 SYN 扫描 + 外网 UDP 洪水 + C2。

        intensity: low=低强度（100~400包/目标、扫描1~3受害） med=中（现网级）
                   high=高强度（2000~5000包/目标，接近训练画像）
        """
        flood_choices = {'low': [100, 200, 300, 400], 'med': [200, 500, 1000, 2000],
                         'high': [2000, 3000, 5000]}[intensity]
        n_scan_victims = 2 if intensity == 'low' else 3
        for v in random.sample(victims, k=min(n_scan_victims, len(victims))):
            for p in SCAN_PORTS:
                self.rows.append(dict(src_ip=attacker, dst_ip=v, src_port=int(np.random.randint(50000, 60000)),
                                      dst_port=p, protocol=6, packets=int(np.random.randint(2, 5)),
                                      bytes=int(np.random.randint(2, 5)) * 60, tcp_flags=2,
                                      ts_start=self.t0 + 20, ts_end=self.t0 + 20 + 8,
                                      label_class=L_MIRAI_FULL))
        for ext in random.sample(EXT_FLOOD, k=2):
            # 与固件真实形态一致：外网洪水每个包随机目的端口 -> 大量 1 包小流
            # （固件 doAttack: esp_random()%64000 每包一个随机端口，源端口固定）
            pkts = int(np.random.choice(flood_choices))
            sport = int(np.random.randint(30000, 40000))
            for _ in range(pkts):
                self.rows.append(dict(src_ip=attacker, dst_ip=ext, src_port=sport,
                                      dst_port=int(np.random.randint(1024, 65535)), protocol=17,
                                      packets=1, bytes=int(np.random.uniform(512, 1400)),
                                      tcp_flags=0, ts_start=self.t0 + 30, ts_end=self.t0 + 30 + 30,
                                      label_class=L_MIRAI_FULL))
        self.rows.append(dict(src_ip=attacker, dst_ip=C2_SERVER, src_port=int(np.random.randint(50000, 60000)),
                              dst_port=443, protocol=6, packets=int(np.random.randint(20, 60)),
                              bytes=int(np.random.randint(20, 60)) * 200, tcp_flags=24,
                              ts_start=self.t0 + 35, ts_end=self.t0 + 35 + 60,
                              label_class=L_MIRAI_FULL))

    def attack_flood_only(self, attacker):
        """纯外网 UDP 洪水（无内网扫描）—— 覆盖现固件升级前的攻击形态。

        外网洪水每个包随机目的端口（与固件真实形态一致，见 attack_mirai 注）。
        """
        for ext in random.sample(EXT_FLOOD, k=2):
            pkts = int(np.random.choice([150, 300, 600, 1000]))
            sport = int(np.random.randint(30000, 40000))
            for _ in range(pkts):
                self.rows.append(dict(src_ip=attacker, dst_ip=ext, src_port=sport,
                                      dst_port=int(np.random.randint(1024, 65535)), protocol=17,
                                      packets=1, bytes=int(np.random.uniform(512, 1400)),
                                      tcp_flags=0, ts_start=self.t0 + 30, ts_end=self.t0 + 30 + 30,
                                      label_class=L_MIRAI_UDP))

    def attack_dos(self, attacker, victim):
        """DoS：对受害机 TCP SYN 洪水。"""
        self.rows.append(dict(src_ip=attacker, dst_ip=victim, src_port=int(np.random.randint(40000, 50000)),
                              dst_port=random.choice([80, 443]), protocol=6,
                              packets=int(np.random.choice([500, 1000, 2000])), bytes=int(np.random.choice([500, 1000, 2000])) * 60,
                              tcp_flags=2, ts_start=self.t0 + 20, ts_end=self.t0 + 20 + 30,
                              label_class=L_DOS))

    def attack_recon(self, attacker, targets):
        """侦察：仅内网 SYN 端口扫描（无洪水/C2）。"""
        for v in random.sample(targets, k=min(4, len(targets))):
            for p in SCAN_PORTS:
                self.rows.append(dict(src_ip=attacker, dst_ip=v, src_port=int(np.random.randint(50000, 60000)),
                                      dst_port=p, protocol=6, packets=int(np.random.randint(2, 4)),
                                      bytes=int(np.random.randint(2, 4)) * 60, tcp_flags=2,
                                      ts_start=self.t0 + 20, ts_end=self.t0 + 20 + 6,
                                      label_class=L_RECON))


def main(out_path='training/data/env/env_training_flows.csv'):
    rows = []
    win_idx = 0

    def gen_window(devices, scenario, minimal=False, intensity='med'):
        nonlocal win_idx
        w = WindowGen(win_idx, devices)
        for dev in devices:
            octet = int(dev.split('.')[-1])
            role = CORE_ROLES.get(octet, 'generic')
            w.benign_device(dev, role, minimal=minimal)
        if scenario == 'benign':
            pass
        elif scenario == 'fw_attack':
            w.attack_firmware(devices[1], random.choice(VICTIMS))
        elif scenario == 'mirai':
            w.attack_mirai(devices[1], devices[2:] + VICTIMS, intensity=intensity)
        elif scenario == 'flood_only':
            w.attack_flood_only(devices[1])
        elif scenario == 'dos':
            w.attack_dos(devices[3], random.choice(VICTIMS))
        elif scenario == 'recon':
            w.attack_recon(devices[4], devices[:4])
        w.gateway()
        rows.extend(w.rows)
        win_idx += 1

    def extended_devices(n):
        """扩展社区设备（.20 起，通用角色）。"""
        return [f'192.168.137.{i}' for i in range(20, 20 + n)]

    # 正常窗口：rich / minimal（现固件最简形态）/ 大小社区混合
    for i in range(400):
        if i % 5 == 0:
            devs = CORE + extended_devices(int(np.random.randint(6, 24)))
        else:
            devs = CORE
        gen_window(devs, 'benign', minimal=(i % 4 == 3))

    # 攻击窗口：四种形态，Mirai 分低/中/高三档强度
    for i in range(150):
        devs = CORE if i % 3 else CORE + extended_devices(6)
        gen_window(devs, 'fw_attack')                    # 现固件攻击（内网 UDP 9000 洪水）
    for i in range(150):
        devs = CORE if i % 3 else CORE + extended_devices(6)
        intensity = 'low' if i % 3 == 0 else ('high' if i % 3 == 1 else 'med')
        gen_window(devs, 'mirai', intensity=intensity)   # 完整 Mirai（扫描+洪水+C2）
    for i in range(80):
        gen_window(CORE, 'flood_only')                   # 纯外网洪水（无扫描）
    for i in range(100):
        gen_window(CORE, 'dos')                          # DoS
    for i in range(80):
        gen_window(CORE, 'recon')                        # 侦察

    df = pd.DataFrame(rows)
    df = df[['src_ip', 'dst_ip', 'src_port', 'dst_port', 'protocol', 'packets',
             'bytes', 'tcp_flags', 'ts_start', 'ts_end', 'label_class']]
    df.to_csv(out_path, index=False)
    print(f'生成 {len(df)} 行 -> {out_path}')
    print(df['label_class'].value_counts().to_string())


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'training/data/env/env_training_flows.csv')
