"""
社区设备注册表 —— 虚拟/物理设备的权威清单，供「流量生成器」与「设备登记脚本」共用。

与 docs/12-community-simulation.md 对齐：6 台物理 ESP32 + 30 台虚拟设备 = 37 节点拓扑。

虚拟设备没有真实 MAC，用 mac_for(device_id) 稳定生成「本地管理单播地址」，
保证生成器发的心跳 MAC 与登记脚本写入的 MAC 一致，从而跳过「发现→认领」。
物理设备真实 MAC 由 ESP32 固件自报，走标准「发现→认领」流程，不在此清单登记。
"""
import hashlib

COMMUNITY_SUBNET = '192.168.4.'
BROKER = '127.0.0.1'
BROKER_PORT = 1884

# 物理设备：(device_id, 类型, IP)
PHYSICAL_DEVICES = [
    ('camera-01', 'camera', '192.168.4.10'),
    ('door-01', 'door', '192.168.4.11'),
    ('light-01', 'light', '192.168.4.12'),
    ('plug-01', 'plug', '192.168.4.13'),
    ('sensor-01', 'temp_sensor', '192.168.4.14'),
    ('speaker-01', 'speaker', '192.168.4.15'),
]

# 虚拟设备：(device_id, 类型, IP, 是否监听CoAP, 联动目标列表)
# 类型：camera/door/light/plug/temp_sensor/speaker/door_sensor/smoke/corridor_light
# 联动原则：同楼栋内（门磁→楼道灯、烟雾→门禁+楼道灯、温湿度→插座、门禁→音箱）
VIRTUAL_DEVICES = [
    # ============ 楼栋 1（物理 door-01 .11, sensor-01 .14 在此）============
    ('ds-01', 'door_sensor',     '192.168.4.104', False, ['192.168.4.113']),
    ('ds-02', 'door_sensor',     '192.168.4.105', False, ['192.168.4.114']),
    ('sm-01', 'smoke',           '192.168.4.110', False, ['192.168.4.11', '192.168.4.113', '192.168.4.114', '192.168.4.115', '192.168.4.124']),
    ('lt-01', 'corridor_light',  '192.168.4.113', True,  []),
    ('lt-02', 'corridor_light',  '192.168.4.114', True,  []),
    ('lt-03', 'corridor_light',  '192.168.4.115', True,  []),

    # ============ 楼栋 2（全虚拟）============
    ('door-02', 'door',          '192.168.4.100', True,  ['192.168.4.129', '192.168.4.122']),
    ('ts-02',   'temp_sensor',   '192.168.4.102', False, ['192.168.4.126']),
    ('ds-03',   'door_sensor',   '192.168.4.106', False, ['192.168.4.116']),
    ('ds-04',   'door_sensor',   '192.168.4.107', False, ['192.168.4.117']),
    ('sm-02',   'smoke',         '192.168.4.111', False, ['192.168.4.100', '192.168.4.116', '192.168.4.117', '192.168.4.118', '192.168.4.125']),
    ('lt-04',   'corridor_light','192.168.4.116', True,  []),
    ('lt-05',   'corridor_light','192.168.4.117', True,  []),
    ('lt-06',   'corridor_light','192.168.4.118', True,  []),

    # ============ 楼栋 3（全虚拟）============
    ('door-03', 'door',          '192.168.4.101', True,  ['192.168.4.129', '192.168.4.123']),
    ('ts-03',   'temp_sensor',   '192.168.4.103', False, ['192.168.4.127']),
    ('ds-05',   'door_sensor',   '192.168.4.108', False, ['192.168.4.119']),
    ('ds-06',   'door_sensor',   '192.168.4.109', False, ['192.168.4.120']),
    ('sm-03',   'smoke',         '192.168.4.112', False, ['192.168.4.101', '192.168.4.119', '192.168.4.120', '192.168.4.121']),
    ('lt-07',   'corridor_light','192.168.4.119', True,  []),
    ('lt-08',   'corridor_light','192.168.4.120', True,  []),
    ('lt-09',   'corridor_light','192.168.4.121', True,  []),

    # ============ 公共区域 ============
    ('cam-02',  'camera',        '192.168.4.122', True,  []),
    ('cam-03',  'camera',        '192.168.4.123', True,  []),
    ('light-02','light',         '192.168.4.124', True,  []),
    ('light-03','light',         '192.168.4.125', True,  []),
    ('plug-02', 'plug',          '192.168.4.126', True,  []),
    ('plug-03', 'plug',          '192.168.4.127', True,  []),
    ('plug-04', 'plug',          '192.168.4.128', True,  []),
    ('speaker-02', 'speaker',    '192.168.4.129', True,  []),
]


def mac_for(device_id: str) -> str:
    """从 device_id 稳定生成本地管理单播 MAC（02:XX:XX:XX:XX:XX，大写）。

    大写是为了与后端 ``_normalize_mac`` 的规范形式一致，避免心跳身份比对时
    因大小写不一致被误判为 identity_mismatch。
    """
    digest = hashlib.sha256(device_id.encode('utf-8')).digest()
    octets = list(digest[:6])
    octets[0] = (octets[0] & 0xFC) | 0x02  # 本地管理、单播（bit0=0, bit1=1）
    return ':'.join(f'{o:02X}' for o in octets)
