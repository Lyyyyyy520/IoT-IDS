"""
Raspberry Pi Probe Client — remote control + real traffic push
Usage: IOT_IDS_PROBE_TOKEN=... python3 probe_client.py --server http://192.168.0.100:5000 --name Pi-001
"""
import argparse, os, time, requests, subprocess, re

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--server', default='http://localhost:5000')
    p.add_argument('--name', default='Pi-001')
    p.add_argument('--interval', type=int, default=5)
    p.add_argument('--token', default=os.getenv('IOT_IDS_PROBE_TOKEN', ''))
    args = p.parse_args()

    if not args.token:
        raise SystemExit('Missing probe credential: set IOT_IDS_PROBE_TOKEN or pass --token')

    session = requests.Session()
    session.headers.update({'X-Probe-Token': args.token})

    print(f'Probe: {args.name} -> {args.server}')

    # Clean start + register
    subprocess.run(['sudo', 'pkill', '-f', 'tcpdump'], capture_output=True)
    try:
        r = session.post(f'{args.server}/api/probe/register', json={'name': args.name})
        session.post(f'{args.server}/api/probe/status-report', json={'name': args.name, 'status': 'stopped'})
        if r.ok: print('Registered OK')
    except Exception as e:
        print(f'Register failed: {e}')
        return

    capturing = False
    pat = re.compile(r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s*>\s*(\d+\.\d+\.\d+\.\d+)\.(\d+)')

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
                subprocess.run(['sudo', 'pkill', '-f', 'tcpdump'], capture_output=True)
                session.post(f'{args.server}/api/probe/status-report', json={'name': args.name, 'status': 'stopped'}, timeout=3)

            if capturing:
                try:
                    raw = subprocess.run(['sudo', 'timeout', '3', 'tcpdump', '-i', 'eth0', '-c', '5', '-n', '-tt'],
                                       capture_output=True, text=True, timeout=5)
                    flows = []
                    for line in raw.stdout.split('\n'):
                        m = pat.search(line)
                        if m:
                            flows.append({
                                'src_ip': m.group(1), 'dst_ip': m.group(3),
                                'src_port': int(m.group(2)), 'dst_port': int(m.group(4)),
                                'protocol': 'TCP' if 'TCP' in line.upper() else 'UDP',
                                'length': 100, 'flags': '', 'source': 'real',
                            })
                    if flows:
                        session.post(f'{args.server}/api/probe/push', json={'probe_name': args.name, 'flows': flows, 'alerts': []}, timeout=3)
                except Exception:
                    pass

            session.post(f'{args.server}/api/probe/heartbeat', json={'name': args.name}, timeout=3)
        except Exception as e:
            print(f'Error: {e}')
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
