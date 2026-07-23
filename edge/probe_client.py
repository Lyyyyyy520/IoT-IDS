"""
Raspberry Pi Probe Client — remote control + real traffic push
Usage: python3 probe_client.py --server http://192.168.0.100:5000 --name Pi-001
"""
import argparse, time, requests, subprocess, re

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--server', default='http://localhost:5000')
    p.add_argument('--name', default='Pi-001')
    p.add_argument('--interval', type=int, default=5)
    args = p.parse_args()

    print(f'Probe: {args.name} -> {args.server}')

    # Clean start + register
    subprocess.run(['sudo', 'pkill', '-f', 'tcpdump'], capture_output=True)
    try:
        r = requests.post(f'{args.server}/api/probe/register', json={'name': args.name})
        requests.post(f'{args.server}/api/probe/status-report', json={'name': args.name, 'status': 'stopped'})
        if r.ok: print('Registered OK')
    except Exception as e:
        print(f'Register failed: {e}')
        return

    capturing = False
    pat = re.compile(r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s*>\s*(\d+\.\d+\.\d+\.\d+)\.(\d+)')

    while True:
        try:
            r = requests.get(f'{args.server}/api/probe/control-status', params={'name': args.name}, timeout=3)
            cmd = r.json().get('action', 'stop')

            if cmd == 'start' and not capturing:
                print('Capture STARTED')
                capturing = True
                requests.post(f'{args.server}/api/probe/status-report', json={'name': args.name, 'status': 'running'}, timeout=3)
            elif cmd == 'stop' and capturing:
                print('Capture STOPPED')
                capturing = False
                subprocess.run(['sudo', 'pkill', '-f', 'tcpdump'], capture_output=True)
                requests.post(f'{args.server}/api/probe/status-report', json={'name': args.name, 'status': 'stopped'}, timeout=3)

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
                        requests.post(f'{args.server}/api/probe/push', json={'probe_name': args.name, 'flows': flows, 'alerts': []}, timeout=3)
                except Exception:
                    pass

            requests.post(f'{args.server}/api/probe/heartbeat', json={'name': args.name}, timeout=3)
        except Exception as e:
            print(f'Error: {e}')
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
