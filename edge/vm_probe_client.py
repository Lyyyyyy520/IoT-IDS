"""VMware IoT traffic probe.

Capture packets on an Ubuntu VM interface and push them directly into the
Windows IoT-IDS backend. The backend runs the same rule, model, logging and
alert pipeline that local capture uses.

Example:
  sudo ./venv/bin/python vm_probe_client.py \
      --server http://192.168.41.1:5000 \
      --name Pi-001 \
      --interface ens33 \
      --bpf "host 192.168.41.136 and not tcp port 5000"
"""
import argparse
from datetime import datetime, timezone
import os
import time
from typing import Dict, List, Optional
from uuid import uuid4

import requests
from scapy.all import ICMP, IP, TCP, UDP, Raw, sniff


def packet_to_flow(pkt) -> Optional[Dict]:
    if IP not in pkt:
        return None

    ip = pkt[IP]
    protocol = ''
    src_port = 0
    dst_port = 0
    flags = ''

    if TCP in pkt:
        protocol = 'TCP'
        src_port = int(pkt[TCP].sport)
        dst_port = int(pkt[TCP].dport)
        flags = str(pkt[TCP].flags)
    elif UDP in pkt:
        protocol = 'UDP'
        src_port = int(pkt[UDP].sport)
        dst_port = int(pkt[UDP].dport)
    elif ICMP in pkt:
        protocol = 'ICMP'
    else:
        return None

    payload = ''
    if Raw in pkt:
        try:
            payload = bytes(pkt[Raw].load[:512]).decode('utf-8', errors='ignore')
        except Exception:
            payload = ''

    return {
        'occurred_at': datetime.fromtimestamp(
            float(pkt.time), tz=timezone.utc
        ).isoformat().replace('+00:00', 'Z'),
        'src_ip': ip.src,
        'dst_ip': ip.dst,
        'src_port': src_port,
        'dst_port': dst_port,
        'protocol': protocol,
        'network_protocol': protocol,
        'length': len(pkt),
        'bytes': len(pkt),
        'packets': 1,
        'flow_count': 0,
        'flags': flags,
        'payload': payload,
        'source': 'real',
    }


def post_json(session: requests.Session, url: str, data: dict, timeout: float = 4.0):
    response = session.post(url, json=data, timeout=timeout)
    response.raise_for_status()
    return response.json() if response.content else {}


def main():
    parser = argparse.ArgumentParser(description='VMware IoT-IDS live traffic probe')
    parser.add_argument('--server', required=True, help='IoT-IDS backend, e.g. http://192.168.41.1:5000')
    parser.add_argument('--name', default='Pi-001', help='Probe name; Pi-001 matches the existing web control')
    parser.add_argument('--interface', default='ens33', help='Ubuntu capture interface')
    parser.add_argument('--bpf', default='not tcp port 5000', help='BPF capture filter')
    parser.add_argument('--poll', type=float, default=1.0, help='Control poll/capture slice duration in seconds')
    parser.add_argument('--batch-size', type=int, default=100, help='Maximum packets per push batch')
    parser.add_argument('--autostart', action='store_true', help='Capture immediately without waiting for web Start')
    parser.add_argument('--token', default=os.getenv('IOT_IDS_PROBE_TOKEN', ''), help='Probe credential')
    args = parser.parse_args()

    if not args.token:
        raise SystemExit('[VM Probe] missing credential: set IOT_IDS_PROBE_TOKEN or pass --token')

    base = args.server.rstrip('/')
    session = requests.Session()
    session.headers.update({'X-Probe-Token': args.token})

    print(f'[VM Probe] name      : {args.name}')
    print(f'[VM Probe] backend   : {base}')
    print(f'[VM Probe] interface : {args.interface}')
    print(f'[VM Probe] filter    : {args.bpf}')

    try:
        registration = post_json(session, f'{base}/api/probe/register', {'name': args.name})
        probe_id = registration.get('probe_id')
        post_json(
            session,
            f'{base}/api/probe/status-report',
            {'name': args.name, 'status': 'running' if args.autostart else 'stopped'},
        )
        print(f'[VM Probe] registered: probe_id={probe_id}')
    except Exception as exc:
        raise SystemExit(f'[VM Probe] cannot reach backend: {exc}')

    capturing = bool(args.autostart)
    total_sent = 0
    source_session_id = uuid4().hex
    batch_sequence = 0
    sample_sequence = 0

    try:
        while True:
            if not args.autostart:
                try:
                    response = session.get(
                        f'{base}/api/probe/control-status',
                        params={'name': args.name},
                        timeout=4,
                    )
                    response.raise_for_status()
                    wanted = response.json().get('action', 'stop') == 'start'
                except Exception as exc:
                    print(f'[VM Probe] control error: {exc}')
                    wanted = capturing

                if wanted != capturing:
                    capturing = wanted
                    state = 'running' if capturing else 'stopped'
                    try:
                        post_json(
                            session,
                            f'{base}/api/probe/status-report',
                            {'name': args.name, 'status': state},
                        )
                    except Exception:
                        pass
                    print(f'[VM Probe] capture {"STARTED" if capturing else "STOPPED"}')

            if capturing:
                flows: List[Dict] = []

                def handler(pkt):
                    flow = packet_to_flow(pkt)
                    if flow is not None:
                        flows.append(flow)

                try:
                    sniff(
                        iface=args.interface,
                        filter=args.bpf or None,
                        prn=handler,
                        store=False,
                        timeout=max(0.2, args.poll),
                    )
                except PermissionError:
                    raise SystemExit('[VM Probe] packet capture needs root. Run this script with sudo.')
                except Exception as exc:
                    print(f'[VM Probe] sniff error: {exc}')
                    time.sleep(1)
                    continue

                for start in range(0, len(flows), max(1, args.batch_size)):
                    batch = flows[start:start + args.batch_size]
                    for flow in batch:
                        flow['sample_id'] = f'{source_session_id}-{sample_sequence}'
                        sample_sequence += 1
                    batch_id = f'{source_session_id}-{batch_sequence}'
                    envelope = {
                        'schema_version': 2,
                        'source_id': f'probe:{probe_id}',
                        'source_session_id': source_session_id,
                        'batch_id': batch_id,
                        'batch_sequence': batch_sequence,
                        'probe_id': probe_id,
                        'probe_name': args.name,
                        'flows': batch,
                        'alerts': [],
                    }
                    batch_sequence += 1
                    try:
                        result = post_json(
                            session,
                            f'{base}/api/probe/push',
                            envelope,
                            timeout=8,
                        )
                        total_sent += int(result.get('flows_received', len(batch)))
                    except Exception as exc:
                        print(f'[VM Probe] push error: {exc}')
                        break

                if flows:
                    print(f'[VM Probe] captured={len(flows):4d}  total_sent={total_sent}')
            else:
                time.sleep(max(0.2, args.poll))

            try:
                post_json(
                    session,
                    f'{base}/api/probe/heartbeat',
                    {'name': args.name, 'probe_id': probe_id},
                )
            except Exception:
                pass

    except KeyboardInterrupt:
        print('\n[VM Probe] stopping...')
    finally:
        try:
            post_json(
                session,
                f'{base}/api/probe/status-report',
                {'name': args.name, 'status': 'stopped'},
            )
        except Exception:
            pass
        print(f'[VM Probe] total packets pushed: {total_sent}')


if __name__ == '__main__':
    main()
