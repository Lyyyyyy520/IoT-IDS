"""
Generate a test PCAP file with simulated IoT device traffic.

Contains realistic Ethernet/IP/TCP packets that mimic:
- Normal IoT device communication (MQTT, HTTP, DNS)
- Mirai botnet scanning patterns (SYN floods to port 23)
- Gafgyt DDoS patterns (UDP floods)

Output: test_traffic.pcap
"""
import struct
import socket
import random
import os

# ===============================================================
# PCAP File Format Writers
# ===============================================================

def pcap_global_header():
    """24-byte PCAP global header (big-endian)."""
    return struct.pack(
        '<IHHiIII',
        0xa1b2c3d4,  # Magic number
        2, 4,         # Version
        0,             # Timezone
        0,             # Sigfigs
        65535,         # Snaplen
        1,             # Link type: Ethernet
    )

def pcap_packet_header(ts_sec, ts_usec, incl_len, orig_len=None):
    """16-byte PCAP packet record header."""
    if orig_len is None:
        orig_len = incl_len
    return struct.pack('<IIII', ts_sec, ts_usec, incl_len, orig_len)

# ===============================================================
# Network Packet Builders
# ===============================================================

def mac_to_bytes(mac_str):
    return bytes.fromhex(mac_str.replace(':', ''))

def ip_to_bytes(ip_str):
    return socket.inet_aton(ip_str)

def checksum(data):
    """16-bit one's complement checksum."""
    if len(data) % 2:
        data += b'\x00'
    s = sum(struct.unpack('!%dH' % (len(data) // 2), data))
    s = (s >> 16) + (s & 0xffff)
    s = s + (s >> 16)
    return struct.pack('!H', ~s & 0xffff)

def build_ethernet(src_mac, dst_mac, ethertype=0x0800):
    """14-byte Ethernet II header."""
    return mac_to_bytes(dst_mac) + mac_to_bytes(src_mac) + struct.pack('!H', ethertype)

def build_ip(src_ip, dst_ip, protocol, payload_len, ttl=64):
    """20-byte IPv4 header (no options)."""
    total_len = 20 + payload_len
    header = struct.pack(
        '!BBHHHBBH4s4s',
        0x45,           # Version + IHL
        0,              # DSCP + ECN
        total_len,      # Total length
        random.randint(1, 65535),  # Identification
        0x4000,         # Flags + Fragment offset (Don't Fragment)
        ttl,            # TTL
        protocol,       # Protocol (6=TCP, 17=UDP)
        0,              # Header checksum (placeholder)
        ip_to_bytes(src_ip),
        ip_to_bytes(dst_ip),
    )
    # Calculate checksum over IP header only
    cksum = checksum(header[:20])
    header = header[:10] + cksum + header[12:]
    return header

def build_tcp(src_port, dst_port, seq, ack, flags, payload=b''):
    """20-byte TCP header + payload."""
    data_offset = 5  # 5 * 4 = 20 bytes (no options)
    tcp_header = struct.pack(
        '!HHIIBBHHH',
        src_port, dst_port,
        seq, ack,
        (data_offset << 4),  # Data offset + Reserved
        flags,               # Flags: FIN=1, SYN=2, RST=4, PSH=8, ACK=16
        8192,                # Window size
        0,                   # Checksum (placeholder, simplified)
        0,                   # Urgent pointer
    )
    return tcp_header + payload

def build_udp(src_port, dst_port, payload=b''):
    """8-byte UDP header + payload."""
    length = 8 + len(payload)
    header = struct.pack(
        '!HHHH',
        src_port, dst_port,
        length,
        0,  # Checksum (0 = disabled)
    )
    return header + payload

# ===============================================================
# Packet Generator
# ===============================================================

# MAC addresses for IoT devices in our simulated community
DEVICES = {
    'router':    {'mac': '00:1a:2b:3c:4d:01', 'ip': '192.168.1.1'},
    'camera1':   {'mac': '00:1a:2b:3c:4d:11', 'ip': '192.168.1.10'},
    'camera2':   {'mac': '00:1a:2b:3c:4d:12', 'ip': '192.168.1.11'},
    'door_ctrl': {'mac': '00:1a:2b:3c:4d:21', 'ip': '192.168.1.20'},
    'sensor1':   {'mac': '00:1a:2b:3c:4d:31', 'ip': '192.168.1.30'},
    'sensor2':   {'mac': '00:1a:2b:3c:4d:32', 'ip': '192.168.1.31'},
    'hub':       {'mac': '00:1a:2b:3c:4d:41', 'ip': '192.168.1.40'},
    'attacker1': {'mac': '00:de:ad:be:ef:01', 'ip': '10.99.1.100'},
    'attacker2': {'mac': '00:de:ad:be:ef:02', 'ip': '10.99.1.200'},
    'c2_server': {'mac': '00:de:ad:be:ef:03', 'ip': '172.20.0.50'},
}

PACKETS = []  # List of (timestamp_sec, raw_packet_bytes)
ts_base = 1689200000  # Base timestamp (2023-07-13)

def add_packet(ts_offset, raw_bytes):
    ts = ts_base + ts_offset
    ts_sec = int(ts)
    ts_usec = int((ts - ts_sec) * 1_000_000)
    PACKETS.append((ts_sec, ts_usec, raw_bytes))

def build_and_add(ts_offset, dev_src, dev_dst, proto, src_port, dst_port,
                  flags=0x10, payload=b'', sport=None, dport=None):
    """Build a full Ethernet+IP+TCP+Payload packet and add to list."""
    src = DEVICES[dev_src]
    dst = DEVICES[dev_dst]
    src_port_val = sport if sport else src_port
    dst_port_val = dport if dport else dst_port

    ip_header = build_ip(src['ip'], dst['ip'], proto, 20 + len(payload))
    if proto == 6:  # TCP
        transport = build_tcp(src_port_val, dst_port_val,
                              random.randint(0, 2**32-1),
                              random.randint(0, 2**32-1),
                              flags, payload)
    else:  # UDP
        transport = build_udp(src_port_val, dst_port_val, payload)

    eth = build_ethernet(src['mac'], dst['mac'])
    raw = eth + ip_header + transport
    add_packet(ts_offset, raw)

# ===============================================================
# Generate Traffic (simulating 5 minutes of activity)
# ===============================================================

# --- Normal IoT Traffic ---
for t in range(0, 300):
    # Regular sensor data uploads (every ~10s)
    if t % 10 == 0:
        build_and_add(t, 'sensor1', 'hub', 6, 49152+t, 1883, 0x18,
                      payload=b'\x30\x24\x00\x13MQTT\x00\x04\x02\x00\x3c\x00\x13temp=22.5;humidity=55',
                      sport=random.randint(40000, 50000))
        build_and_add(t, 'sensor2', 'hub', 6, 49153+t, 1883, 0x18,
                      payload=b'\x30\x26\x00\x13MQTT\x00\x04\x02\x00\x3c\x00\x15temp=23.1;humidity=52',
                      sport=random.randint(40000, 50000))

    # Camera streaming heartbeat (every 5s)
    if t % 5 == 0:
        build_and_add(t, 'camera1', 'router', 6, 49154+t, 80, 0x18,
                      payload=b'GET /api/stream/status HTTP/1.1\r\nHost: 192.168.1.1\r\n\r\n',
                      sport=random.randint(40000, 50000))

    # Door controller status check (every 15s)
    if t % 15 == 0:
        build_and_add(t, 'door_ctrl', 'router', 6, 49155+t, 80, 0x18,
                      payload=b'POST /api/door/status HTTP/1.1\r\nHost: 192.168.1.1\r\nContent-Length: 12\r\n\r\ndoor=locked',
                      sport=random.randint(40000, 50000))

    # DNS queries (every 20s)
    if t % 20 == 0:
        build_and_add(t, 'hub', 'router', 17, 53, 53,
                      payload=b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07iot-hub\x05local\x00\x00\x01\x00\x01',
                      sport=random.randint(40000, 50000), dport=53)

    # --- Mirai Botnet Patterns (SYN scan to port 23) ---
    if t % 30 == 0 and t > 60:
        for i in range(5):
            target_ip = f'192.168.1.{10 + i}'
            DEVICES[f'target{i}'] = {'mac': f'00:1a:2b:3c:4d:5{i}', 'ip': target_ip}
            build_and_add(t + i*0.1, 'attacker1', f'target{i}', 6, random.randint(50000, 60000), 23, 0x02,
                          sport=random.randint(50000, 60000))
            del DEVICES[f'target{i}']

    # --- Gafgyt UDP Flood ---
    if t % 45 == 0 and t > 90:
        for i in range(8):
            build_and_add(t + i*0.05, 'attacker2', 'router', 17, random.randint(30000, 40000), 80,
                          payload=b'\x00' * 64,
                          sport=random.randint(30000, 40000), dport=80)

    # --- Other Attack Pattern ---
    if t % 60 == 0 and t > 120:
        build_and_add(t, 'attacker2', 'c2_server', 6, 52341+t, 46370, 0x18,
                      payload=b'\x00\x00\x00\x10\x02\x00\x00\x00\x01\x00\x00\x00version=2.1',
                      sport=random.randint(50000, 60000))
        for j in range(3):
            target_ip = f'172.20.0.{10+j}'
            DEVICES[f'peer{j}'] = {'mac': f'00:de:ad:be:ef:1{j}', 'ip': target_ip}
            build_and_add(t + j*0.2, 'attacker2', f'peer{j}', 6, 52342+j, 46370, 0x02,
                          sport=random.randint(50000, 60000))
            del DEVICES[f'peer{j}']

    # --- Normal device ARP requests (simplified as small Ethernet frames) ---
    if t % 25 == 0:
        eth_only = mac_to_bytes('ff:ff:ff:ff:ff:ff') + mac_to_bytes(DEVICES['router']['mac']) + struct.pack('!H', 0x0806)
        add_packet(t, eth_only + b'\x00\x01\x08\x00\x06\x04\x00\x01' + mac_to_bytes(DEVICES['router']['mac']) +
                   ip_to_bytes(DEVICES['router']['ip']) + mac_to_bytes('00:00:00:00:00:00') +
                   ip_to_bytes(f'192.168.1.{t%250+2}'))

# ===============================================================
# Write PCAP File
# ===============================================================

output_path = os.path.join(os.path.dirname(__file__), '..', 'test_traffic.pcap')

# Sort by timestamp
PACKETS.sort(key=lambda x: (x[0], x[1]))

with open(output_path, 'wb') as f:
    f.write(pcap_global_header())

    for ts_sec, ts_usec, raw in PACKETS:
        f.write(pcap_packet_header(ts_sec, ts_usec, len(raw)))

print(f'Generated test PCAP: {output_path}')
print(f'Total packets: {len(PACKETS)}')
print(f'File size: {os.path.getsize(output_path):,} bytes')
print(f'Ready for upload at http://localhost:3000')
