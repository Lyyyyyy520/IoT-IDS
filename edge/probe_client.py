"""
Raspberry Pi Probe Client — Simulates a Suricata-based probe node

Sends periodic heartbeat + pushes alerts/flows to the Windows management server.

Usage:
  python probe_client.py --server http://192.168.0.100:5000 --name Pi-LivingRoom
"""
import argparse
import time
import random
import requests
from datetime import datetime

# ---- Mock Suricata-style alert generation ----
ALERT_TEMPLATES = [
    {
        'risk_level': 'critical',
        'attack_type': 'Mirai',
        'src_ip': '192.168.1.105',
        'dst_ip': '192.168.1.10',
        'src_port': 55432,
        'dst_port': 23,
        'protocol': 'TCP',
        'confidence': 0.96,
        'description': 'Suricata: ET MALWARE Mirai Botnet Scan detected',
    },
    {
        'risk_level': 'high',
        'attack_type': 'Gafgyt',
        'src_ip': '10.0.0.45',
        'dst_ip': '192.168.1.1',
        'src_port': 40123,
        'dst_port': 80,
        'protocol': 'UDP',
        'confidence': 0.89,
        'description': 'Suricata: ET DDoS Gafgyt UDP Flood detected',
    },
    {
        'risk_level': 'high',
        'attack_type': 'PortScan',
        'src_ip': '172.16.0.88',
        'dst_ip': '192.168.1.20',
        'src_port': 51234,
        'dst_port': 22,
        'protocol': 'TCP',
        'confidence': 0.92,
        'description': 'Suricata: ET SCAN NMAP TCP Scan detected',
    },
    {
        'risk_level': 'medium',
        'attack_type': 'BruteForce',
        'src_ip': '10.99.1.200',
        'dst_ip': '192.168.1.30',
        'src_port': 60001,
        'dst_port': 22,
        'protocol': 'TCP',
        'confidence': 0.85,
        'description': 'Suricata: ET EXPLOIT SSH Brute Force Login',
    },
    {
        'risk_level': 'medium',
        'attack_type': 'Other',
        'src_ip': '172.20.0.50',
        'dst_ip': '192.168.1.40',
        'src_port': 46370,
        'dst_port': 46370,
        'protocol': 'TCP',
        'confidence': 0.78,
        'description': 'Suricata: ET Other Suspicious Activity',
    },
]


def generate_flows(count=5):
    """Generate random normal traffic flows."""
    flows = []
    devices = [f'192.168.1.{i}' for i in range(10, 50)]
    for _ in range(count):
        flows.append({
            'src_ip': random.choice(devices),
            'dst_ip': '192.168.1.1',
            'src_port': random.randint(40000, 50000),
            'dst_port': random.choice([80, 443, 1883, 5683, 53]),
            'protocol': random.choice(['TCP', 'UDP']),
            'length': random.randint(60, 1500),
            'flags': random.choice(['SYN', 'ACK', 'PSH', '']),
        })
    return flows


def main():
    parser = argparse.ArgumentParser(description='IoT IDS Probe Client')
    parser.add_argument('--server', default='http://localhost:5000', help='Management server URL')
    parser.add_argument('--name', default='Pi-Probe-01', help='Probe name')
    parser.add_argument('--interval', type=int, default=15, help='Push interval (seconds)')
    args = parser.parse_args()

    print(f'Probe Client — {args.name}')
    print(f'Server: {args.server}')
    print(f'Push interval: {args.interval}s')
    print('-' * 50)

    # Register with server
    try:
        r = requests.post(f'{args.server}/api/probe/register', json={'name': args.name})
        if r.ok:
            probe_id = r.json().get('probe_id')
            print(f'Registered: probe_id={probe_id}')
        else:
            print(f'Register failed: {r.status_code}')
            probe_id = None
    except requests.ConnectionError:
        print(f'ERROR: Cannot connect to {args.server}')
        return

    # Main loop
    push_count = 0
    while True:
        try:
            time.sleep(args.interval)
            push_count += 1

            # Heartbeat
            requests.post(f'{args.server}/api/probe/heartbeat', json={'probe_id': probe_id})

            # Generate random alerts (30% chance per cycle)
            alerts = []
            if random.random() < 0.3:
                alert = random.choice(ALERT_TEMPLATES).copy()
                alert['description'] = f'[Probe:{args.name}] {alert["description"]}'
                alerts = [alert]

            # Generate flows
            flows = generate_flows(random.randint(3, 8))

            # Push data
            payload = {
                'probe_id': probe_id,
                'probe_name': args.name,
                'alerts': alerts,
                'flows': flows,
            }
            r = requests.post(f'{args.server}/api/probe/push', json=payload)
            result = r.json() if r.ok else {}

            now = datetime.now().strftime('%H:%M:%S')
            print(f'[{now}] Push #{push_count} | '
                  f'Alerts: {result.get("alerts_received", 0)} | '
                  f'Flows: {result.get("flows_received", 0)}')

        except requests.ConnectionError:
            print(f'[{datetime.now().strftime("%H:%M:%S")}] Connection lost, retrying...')
        except KeyboardInterrupt:
            print('\nProbe client stopped.')
            break


if __name__ == '__main__':
    main()
