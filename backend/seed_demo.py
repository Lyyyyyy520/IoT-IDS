"""Seed demo data into the database for demonstration purposes."""
import sys, os, random
sys.path.insert(0, os.path.dirname(__file__))
from database import execute

alert_types = [
    ('critical', 'Mirai', 'Mirai 僵尸网络扫描行为'),
    ('critical', 'Mirai', 'Mirai 暴力破解 Telnet 端口'),
    ('high', 'Gafgyt', 'Gafgyt DDoS 攻击流量异常'),
    ('high', 'Gafgyt', 'Gafgyt UDP Flood 攻击'),
    ('medium', 'Other', '其他攻击行为'),
    ('medium', 'Other', 'SSH 暴力破解攻击'),
    ('low', 'Other', '可疑 DNS 查询请求'),
]
src_ips = [f'192.168.1.{i}' for i in range(10, 255, 15)] + [f'10.0.0.{i}' for i in range(1, 20, 3)]

# Seed alerts
for i in range(25):
    t = random.choice(alert_types)
    execute(
        "INSERT INTO alerts (risk_level, attack_type, src_ip, dst_ip, src_port, dst_port, protocol, confidence, description, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now','localtime',?))",
        (t[0], t[1], random.choice(src_ips), '192.168.1.1',
         random.randint(1024, 65535), random.choice([80, 443, 22, 23, 1883]),
         random.choice(['TCP', 'UDP']), round(random.uniform(0.70, 0.99), 2),
         '[sim] ' + t[2], random.choice(['new', 'new', 'new', 'reviewed', 'resolved']),
         f'-{random.randint(0, 1440)} minutes'),
    )

# Seed traffic logs with ONNX labels
onnx_labels = ['normal'] * 20 + ['mirai'] * 5 + ['gafgyt'] * 3 + ['other'] * 2
for i in range(30):
    execute(
        "INSERT INTO traffic_logs (src_ip, dst_ip, src_port, dst_port, protocol, length, flags, onnx_label) VALUES (?,?,?,?,?,?,?,?)",
        (random.choice(src_ips), '192.168.1.1', random.randint(1024, 65535),
         random.choice([80, 443, 1883, 53]), random.choice(['TCP', 'UDP']),
         random.randint(60, 1500), random.choice(['SYN', 'ACK', 'PSH', '']),
         onnx_labels[i]),
    )

print('Seeded 25 alerts + 30 traffic logs')
