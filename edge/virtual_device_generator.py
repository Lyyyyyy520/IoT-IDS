#!/usr/bin/env python3
"""
社区流量生成器 —— 在本地电脑或 Ubuntu 虚拟机里模拟 30 台虚拟 IoT 设备。

生成多协议真实流量（MQTT/DNS/NTP/HTTP）+ 设备间 CoAP 联动，
配合 6 台物理 ESP32，让设备图变成"3 楼栋 + 公共区域"的 37 节点丰富拓扑。

MQTT 心跳符合后端 v3 规范（community/{id}/status 完整 schema）：
  配合 backend/v3_device_seed.py 先登记设备后，心跳 MAC 与登记 MAC 一致，
  设备直接在线、GNN 风险可落库，无需逐台「发现→认领」。

前置（电脑/虚拟机，Linux 用 setup_virtual_ips.sh，Windows 用 setup_virtual_ips.bat）：
    1. pip install paho-mqtt
    2. 给 WiFi 网卡挂 .100~.129 的虚拟 IP
    3. 连上树莓派热点 iot-community

运行：
    python virtual_device_generator.py                        # 全正常
    python virtual_device_generator.py --attack plug-03       # 启动时指定某台被感染

运行时触发攻击（MQTT，与 ESP32 固件控制指令一致）：
    mosquitto_pub -h 192.168.4.1 -t "community/plug-03/control" -m "attack"   # 触发攻击
    mosquitto_pub -h 192.168.4.1 -t "community/plug-03/control" -m "block"    # 隔离/停止
"""

import socket
import threading
import time
import random
import json
import secrets

from community_devices import (
    BROKER, BROKER_PORT, COMMUNITY_SUBNET, VIRTUAL_DEVICES as DEVICES, mac_for,
)

# ==================== 配置 ====================

DNS_SERVER  = '8.8.8.8'       # 外网 DNS
NTP_SERVER  = '192.168.4.1'   # 时间同步（网关）
CLOUD_HTTP  = '8.8.8.8'       # 外网云（HTTP 心跳，占位）
ATTACK_TARGET = '8.8.8.8'     # 攻击目标（Mirai UDP 洪水的外网目标）
SCAN_SUBNET   = COMMUNITY_SUBNET  # 内部网段（攻击扫描目标）
SCAN_RATE     = 0.3           # 扫描概率（可调，现场校准邻居污染）
SCAN_PORTS    = [23, 80, 8080, 2323]  # 常见扫描端口（Mirai 扫 telnet/HTTP）


# ==================== 原始流量发送（DNS/NTP/HTTP/CoAP） ====================

# 关键：DNS/NTP/CoAP 共用一个持久 UDP socket，源端口固定。
# 若每次发包都新建 socket，会疯狂换源端口，被模型误判成"扫描/僵尸网络"。

def send_dns(udp_sock):
    """DNS 查询（UDP 53），复用持久 socket。"""
    try:
        udp_sock.sendto(b'\x00\x01\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x03www\x06google\x03com\x00'
                        b'\x00\x01\x00\x01', (DNS_SERVER, 53))
    except Exception:
        pass


def send_ntp(udp_sock):
    """NTP 时间同步（UDP 123），复用持久 socket。"""
    try:
        udp_sock.sendto(b'\x1b' + b'\x00' * 47, (NTP_SERVER, 123))
    except Exception:
        pass


def send_coap(udp_sock, target_ip):
    """设备间联动事件（CoAP, UDP 5683），复用持久 socket。"""
    try:
        udp_sock.sendto(b'evt', (target_ip, 5683))
    except Exception:
        pass


def send_http(src_ip):
    """HTTP 心跳（TCP 80，连接即产生 SYN 流量，用于特征）。"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((src_ip, 0))
        sock.settimeout(2)
        sock.connect((CLOUD_HTTP, 80))
        sock.send(b'GET / HTTP/1.0\r\nHost: cloud\r\n\r\n')
        sock.close()
    except Exception:
        pass  # 连不上也无所谓，SYN 包已经被抓包看到了


# ==================== MQTT（真实协议，paho-mqtt） ====================

def mqtt_loop(device_id, device_type, src_ip, stop, attack_event):
    """每台虚拟设备一个 MQTT 客户端：周期发布完整 v3 心跳 + 订阅控制指令。"""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print('[!] 缺少 paho-mqtt，请先 pip install paho-mqtt')
        return

    client = mqtt.Client(client_id=device_id)

    def on_message(client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode('utf-8', errors='ignore').strip()
        if topic == f'community/{device_id}/control':
            if payload == 'attack':
                attack_event.set()
                print(f'[控制] {device_id} 进入攻击模式（模拟被感染）')
            elif payload in ('normal', 'block'):
                attack_event.clear()
                print(f'[控制] {device_id} 恢复/隔离')

    client.on_message = on_message
    try:
        client.connect(BROKER, BROKER_PORT, keepalive=60)
    except Exception as e:
        print(f'[MQTT] {device_id} 连接失败: {e}')
        return
    client.subscribe(f'community/{device_id}/control')
    client.loop_start()

    boot_id = secrets.token_hex(16)  # 32 hex chars，模拟上电 boot
    start = time.time()
    sequence = 0
    mac = mac_for(device_id)

    while not stop.is_set():
        sequence += 1
        heartbeat = {
            'schema_version': 2,
            'device_id': device_id,
            'boot_id': boot_id,
            'sequence': sequence,
            'firmware_version': '1.0.0',
            'uptime_ms': int((time.time() - start) * 1000),
            'ip': src_ip,
            'mac': mac,
            'telemetry': {'device_type': device_type},
        }
        try:
            client.publish(f'community/{device_id}/status', json.dumps(heartbeat))
        except Exception:
            pass
        time.sleep(random.uniform(5, 15))

    client.loop_stop()


# ==================== CoAP 监听（执行器侧） ====================

def coap_listen(device_id, src_ip, stop):
    """执行器设备监听 5683 端口，接收联动事件。"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((src_ip, 5683))
        sock.settimeout(1.0)
    except Exception as e:
        print(f'[CoAP] {device_id} 监听失败: {e}')
        return

    while not stop.is_set():
        try:
            data, addr = sock.recvfrom(64)
            print(f'[联动] {device_id} 收到 CoAP 事件 (来自 {addr[0]})')
        except socket.timeout:
            continue
        except Exception:
            break


# ==================== 攻击模式（被感染设备） ====================

def attack_worker(device_id, src_ip, attack_event, stop):
    """攻击线程（常驻）：attack_event 触发时执行「内部扫描 + 外部洪水」。

    模拟 Mirai 两阶段：
      阶段1 侦察（轻量内部扫描）：随机探测网段内设备，制造新内部边（图结构突变）
      阶段2 僵尸网络（外部 UDP 洪水）：高流量 + 随机目的端口（节点特征突变）
    双重信号让 GNN 能更准地判「僵尸网络(红)」。未触发时 idle，几乎不占资源。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((src_ip, 0))  # 固定源端口（持久 socket）
    except OSError:
        pass  # 未绑定虚拟 IP 时，攻击流量从默认地址发出（本地测试可接受）
    while not stop.is_set():
        if attack_event.is_set():
            # 阶段1：轻量内部扫描（按 SCAN_RATE 概率，可调）
            if random.random() < SCAN_RATE:
                target = SCAN_SUBNET + str(random.randint(2, 129))
                sock.sendto(b'\x00\x00', (target, random.choice(SCAN_PORTS)))
            # 阶段2：外部 UDP 洪水（随机目的端口，Mirai udpplain 特征）
            for _ in range(8):
                dst_port = random.randint(1, 65535)
                sock.sendto(b'\x00\x00\x00\x00', (ATTACK_TARGET, dst_port))
            time.sleep(0.05)  # ~160 包/秒
        else:
            time.sleep(0.2)  # 未攻击时降低轮询频率
    sock.close()


# ==================== 设备 worker ====================

def device_worker(device_id, device_type, src_ip, listen_coap, coap_targets, is_attacker=False):
    """一台虚拟设备的主循环：MQTT + DNS + NTP + HTTP + CoAP（+ 运行时攻击触发）。"""
    stop = threading.Event()
    attack_event = threading.Event()  # 攻击触发标志（MQTT attack 指令设置）
    if is_attacker:
        attack_event.set()  # 启动时用 --attack 指定的设备立即开火
    threads = []

    # 持久 UDP socket：DNS/NTP/CoAP 共用，源端口固定
    # （避免每次发包新建 socket 导致源端口过多、被模型误判为扫描/僵尸网络）
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp_sock.bind((src_ip, 0))
    except OSError:
        pass  # 未绑定虚拟 IP 时，DNS/NTP/CoAP 流量从默认地址发出

    # MQTT 心跳 + 控制订阅（独立线程）
    t = threading.Thread(target=mqtt_loop, args=(device_id, device_type, src_ip, stop, attack_event), daemon=True)
    t.start()
    threads.append(t)

    # CoAP 监听（执行器）
    if listen_coap:
        t = threading.Thread(target=coap_listen, args=(device_id, src_ip, stop), daemon=True)
        t.start()
        threads.append(t)

    # 攻击线程（常驻，attack_event 触发时才发洪水）
    t = threading.Thread(target=attack_worker, args=(device_id, src_ip, attack_event, stop), daemon=True)
    t.start()
    threads.append(t)

    # 主循环：周期发 DNS/NTP/HTTP/CoAP（带随机抖动）
    try:
        while True:
            time.sleep(random.uniform(1, 3))
            # DNS：约 1~2 次/分钟
            if random.random() < 0.03:
                send_dns(udp_sock)
            # NTP：约 1 次/分钟
            if random.random() < 0.02:
                send_ntp(udp_sock)
            # HTTP：摄像头更频繁（视频流），其他低频
            p_http = 0.08 if device_type == 'camera' else 0.015
            if random.random() < p_http:
                send_http(src_ip)
            # CoAP 联动：传感器周期触发
            if coap_targets and random.random() < 0.03:
                for target in coap_targets:
                    send_coap(udp_sock, target)
    except KeyboardInterrupt:
        pass

    udp_sock.close()
    stop.set()
    for t in threads:
        t.join(timeout=2)


# ==================== main ====================

def main():
    # 解析攻击设备：python virtual_device_generator.py --attack plug-03,cam-02
    import sys
    attack_ids = set()
    if '--attack' in sys.argv:
        idx = sys.argv.index('--attack')
        if idx + 1 < len(sys.argv):
            attack_ids = set(a.strip() for a in sys.argv[idx + 1].split(',') if a.strip())
    if attack_ids:
        print(f'被感染设备（攻击模式）: {", ".join(sorted(attack_ids))}')

    print(f'启动 {len(DEVICES)} 台虚拟设备...\n')
    for device_id, device_type, src_ip, listen_coap, coap_targets in DEVICES:
        is_attacker = device_id in attack_ids
        t = threading.Thread(
            target=device_worker,
            args=(device_id, device_type, src_ip, listen_coap, coap_targets, is_attacker),
            daemon=True,
        )
        t.start()
        print(f'  {device_id:11s} {device_type:14s} {src_ip}  '
              f'{"[监听CoAP]" if listen_coap else ""}{" -> " + ",".join(coap_targets) if coap_targets else ""}')

    print('\n全部启动，Ctrl+C 停止')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n停止')


if __name__ == '__main__':
    main()
