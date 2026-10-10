"""纯固件场景的物理设备联动登记表（独立于虚拟版 community_devices.py）。

承载 LINK_SIM_MODE 固件的联动边：6 台物理板以"角色托管"方式模拟
docs/12 的完整联动关系类别（门磁→楼道灯、烟雾→门禁/应急灯、温湿度→插座、
门禁→音箱/摄像头）。本文件与虚拟版（community_devices.py /
virtual_device_generator.py）完全独立，后端 monitor_snapshot 用
IOT_IDS_TOPO_SCENARIO=auto|virtual|physical 选择加载场景。

边表：PHYSICAL_LINKAGES = [(source, target, 承载角色说明, protocols)]
  - speaker-01 托管门磁 ds-01~06 -> light-01（楼道灯代收）
  - sensor-01 托管烟雾 sm-01~03 -> door-01（解锁）/ light-01（应急灯）
  - sensor-01 温湿度（真实+托管 ts-02/03）-> plug-01（通断电）
  - door-01 门禁（真实+托管 door-02/03）-> speaker-01（播报）/ camera-01（录像）
"""
PHYSICAL_DEVICES = [
    ('camera-01', 'camera', '192.168.4.10'),
    ('door-01', 'door', '192.168.4.11'),
    ('light-01', 'light', '192.168.4.12'),
    ('plug-01', 'plug', '192.168.4.13'),
    ('sensor-01', 'temp_sensor', '192.168.4.14'),
    ('speaker-01', 'speaker', '192.168.4.15'),
]

PHYSICAL_LINKAGES = [
    ('speaker-01', 'light-01', '门磁 ds-01~06 -> 楼道灯（角色托管）', ['coap']),
    ('sensor-01', 'door-01', '烟雾 sm-01~03 -> 门禁解锁（角色托管）', ['coap']),
    ('sensor-01', 'light-01', '烟雾 -> 应急亮灯（角色托管）', ['coap']),
    ('sensor-01', 'plug-01', '温湿度 -> 插座通断电（真实+托管）', ['coap']),
    ('door-01', 'speaker-01', '开门 -> 音箱播报（真实联动）', ['coap']),
    ('door-01', 'camera-01', '开门 -> 摄像头录像（真实联动）', ['coap']),
]
