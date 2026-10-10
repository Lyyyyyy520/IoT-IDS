"""
Raspberry Pi Probe Client — remote control + real traffic push
Usage: IOT_IDS_PROBE_TOKEN=... python3 probe_client.py --server http://192.168.0.100:5000 --name Pi-001
"""
import argparse
from datetime import datetime, timezone
import os
import re
import subprocess
import time
from uuid import uuid4

import requests

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--server', default='http://localhost:5000')
    p.add_argument('--name', default='Pi-001')
    p.add_argument('--interval', type=int, default=5)
    p.add_argument('--token', default=os.getenv('IOT_IDS_PROBE_TOKEN', ''))
    # 设备流量全在 Pi 热点接口 wlan0（板间 CoAP/攻击洪水都不过 eth0）
    p.add_argument('--interface', default='wlan0', help='capture interface (default: wlan0)')
    args = p.parse_args()

    if not args.token:
        raise SystemExit('Missing probe credential: set IOT_IDS_PROBE_TOKEN or pass --token')

    session = requests.Session()
    session.headers.update({'X-Probe-Token': args.token})

    print(f'Probe: {args.name} -> {args.server} (interface {args.interface})')

    # 后端端口（用于 BPF 排除探针自身的上报流量）
    try:
        server_port = int(args.server.rsplit(':', 1)[1].rstrip('/'))
    except (IndexError, ValueError):
        server_port = 5000

    # 注册（不再 pkill tcpdump：probe_client 与 edge_detect.py 可在同一 Pi 共存，
    # 每次抓包都是短命 `timeout 3` 子进程，会自愈退出）
    try:
        r = session.post(f'{args.server}/api/probe/register', json={'name': args.name})
        session.post(f'{args.server}/api/probe/status-report', json={'name': args.name, 'status': 'stopped'})
        registration = r.json() if r.ok else {}
        probe_id = registration.get('probe_id')
        if r.ok: print('Registered OK')
    except Exception as e:
        print(f'Register failed: {e}')
        return

    capturing = False
    pat = re.compile(r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s*>\s*(\d+\.\d+\.\d+\.\d+)\.(\d+)')
    length_pat = re.compile(r'\blength\s+(\d+)\b')
    timestamp_pat = re.compile(r'^([0-9]+(?:\.[0-9]+)?)\s+')
    source_session_id = uuid4().hex
    batch_sequence = 0
    sample_sequence = 0

    while True:
        try:
            r = session.get(f'{args.server}/api/probe/control-status', params={'name': args.name}, timeout=3)
            cmd = r.json().get('action', 'stop')

            if cmd == 'start' and not capturing:
                print('Capture STARTED')
                capturing = True
                session.post(f'{args.server}/api/probe/status-report', json={'name': args.name, 'status': 'running'}, timeout=3)
            elif cmd == 'stop' and capturing:
                print('Capture STOPPED')
                capturing = False
                session.post(f'{args.server}/api/probe/status-report', json={'name': args.name, 'status': 'stopped'}, timeout=3)

            if capturing:
                try:
                    bpf = f'not tcp port {server_port}'
                    raw = subprocess.run(
                        ['sudo', 'timeout', '3', 'tcpdump', '-i', args.interface,
                         '-c', '50', '-n', '-tt', bpf],
                        capture_output=True, text=True, timeout=5)
                    flows = []
                    for line in raw.stdout.split('\n'):
                        m = pat.search(line)
                        length_match = length_pat.search(line)
                        timestamp_match = timestamp_pat.search(line)
                        if m and length_match and timestamp_match:
                            occurred_at = datetime.fromtimestamp(
                                float(timestamp_match.group(1)), tz=timezone.utc
                            ).isoformat().replace('+00:00', 'Z')
                            protocol = 'TCP' if 'TCP' in line.upper() else 'UDP'
                            sample_id = f'{source_session_id}-{sample_sequence}'
                            sample_sequence += 1
                            flows.append({
                                'sample_id': sample_id,
                                'occurred_at': occurred_at,
                                'src_ip': m.group(1), 'dst_ip': m.group(3),
                                'src_port': int(m.group(2)), 'dst_port': int(m.group(4)),
                                'protocol': protocol,
                                'network_protocol': protocol,
                                'length': int(length_match.group(1)),
                                'bytes': int(length_match.group(1)),
                                'packets': 1,
                                'flow_count': 0,
                                'flags': '', 'source': 'real',
                            })
                    if flows:
                        sequence = batch_sequence
                        batch_sequence += 1
                        session.post(
                            f'{args.server}/api/probe/push',
                            json={
                                'schema_version': 2,
                                'source_id': f'probe:{probe_id}',
                                'source_session_id': source_session_id,
                                'batch_id': f'{source_session_id}-{sequence}',
                                'batch_sequence': sequence,
                                'probe_id': probe_id,
                                'probe_name': args.name,
                                'flows': flows,
                                'alerts': [],
                            },
                            timeout=3,
                        )
                except Exception as exc:
                    # 不再静默吞异常：抓包失败（接口不存在/权限）要能看见
                    print(f'Capture failed: {type(exc).__name__}: {exc}')

            session.post(f'{args.server}/api/probe/heartbeat', json={'name': args.name}, timeout=3)
        except Exception as e:
            print(f'Error: {e}')
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
