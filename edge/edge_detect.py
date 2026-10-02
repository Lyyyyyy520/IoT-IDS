"""
Raspberry Pi Edge Detection Program

Runs local ONNX inference with optional tcpdump live capture.
Designed for Raspberry Pi 4B (4GB) with Raspberry Pi OS Bullseye 64-bit.

Usage:
  python edge_detect.py --demo               # Run with mock data demo
  python edge_detect.py --live               # Live capture (requires sudo + Scapy)
"""
import os
import sys
import time
import argparse
from datetime import datetime, timezone
from uuid import uuid4
import numpy as np

# Add parent to path for backend imports (works on Windows and Pi)
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, '..', 'backend'))
sys.path.insert(0, os.path.join(_here, 'backend'))
sys.path.insert(0, '/home/pi/backend')  # Raspberry Pi absolute path

from models.inference import InferenceEngine
from services.feature_extract import FeatureExtractor

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'best_model.onnx')
SCALER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scaler.pkl')


def detect_demo(engine: InferenceEngine, extractor: FeatureExtractor):
    """Run detection with mock feature vectors for demonstration."""
    print('\n' + '=' * 60)
    print('  IoT IDS Edge Detection — Demo Mode')
    print('=' * 60)
    print(f'  Model: {MODEL_PATH}')
    print(f'  Model loaded: {engine.model_loaded}')

    if not engine.model_loaded:
        print('\n[!] Model not found. Run training first.')
        return

    print('\nRunning detection on mock samples...\n')
    samples = extractor._mock_extract(10)

    for i, feat in enumerate(samples):
        result = engine.predict(feat)
        risk_icon = {'critical': '🔴', 'high': '🟠', 'medium': '🟡', 'low': '🟢'}.get(result['risk_level'], '⚪')
        print(f'  [{i+1:2d}] {risk_icon} {result["class_name"]:8s} | '
              f'Conf: {result["confidence"]:.2%} | Risk: {result["risk_level"]}')

    # Performance stats
    print('\n--- Performance Test ---')
    test_feat = samples[0]
    n_runs = 1000
    start = time.time()
    for _ in range(n_runs):
        engine.predict(test_feat)
    elapsed = time.time() - start
    print(f'  {n_runs} inferences in {elapsed:.2f}s')
    print(f'  Avg latency: {elapsed/n_runs*1000:.2f} ms/sample')
    print(f'  Throughput:  {n_runs/elapsed:.0f} samples/sec')


def detect_live(engine: InferenceEngine, extractor: FeatureExtractor,
                server_url: str = None, probe_token: str = None):
    """Capture live packets; report privacy-safe v2 traffic batches to v3."""
    import re
    import subprocess
    import requests

    print('\n' + '=' * 60)
    print('  IoT IDS Edge Detection - Live Capture Mode')
    print('=' * 60)
    print(f'  Model: {MODEL_PATH}')
    print(f'  Model loaded: {engine.model_loaded}')
    print('  Listening on eth0...\n')

    if not engine.model_loaded:
        print('[!] Model not loaded. Exiting.')
        return

    packet_count = [0]
    alert_count = [0]
    source_session_id = str(uuid4())
    batch_sequence = 0
    proc = subprocess.Popen(
        ['sudo', 'tcpdump', '-i', 'eth0', '-l', '-n', '-tt', 'ip'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=0
    )
    ip_re = re.compile(r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s*>\s*(\d+\.\d+\.\d+\.\d+)\.(\d+)')
    length_re = re.compile(r'\blength\s+(\d+)')

    print('Capture started. Press Ctrl+C to stop.\n')
    try:
        for line in iter(proc.stdout.readline, ''):
            match = ip_re.search(line)
            if not match:
                continue
            packet_count[0] += 1
            src_ip, sport, dst_ip, dport = match.group(1), int(match.group(2)), match.group(3), int(match.group(4))
            protocol = 'TCP' if 'TCP' in line.upper() else 'UDP' if 'UDP' in line.upper() else None
            if protocol is None:
                continue
            length_match = length_re.search(line)
            packet_bytes = int(length_match.group(1)) if length_match else 0

            # Keep the local research model visible in the terminal. Its current
            # feature adapter uses placeholder flow statistics, so these scores
            # are deliberately not promoted into real v3 security incidents.
            flow_data = {
                'protocol_type': 1 if protocol == 'TCP' else 2,
                'src_port': sport,
                'dst_port': dport,
                'min_packet_length': packet_bytes,
                'flow_duration': 0.0,
                'flow_bytes_per_sec': 0.0,
                'flow_packets_per_sec': 0.0,
                'syn_count': int(protocol == 'TCP' and 'S' in line),
                'ack_count': int(protocol == 'TCP' and 'A' in line),
            }
            result = engine.predict(extractor.extract_from_flow(flow_data))

            if server_url and packet_count[0] % 5 == 0:
                batch_sequence += 1
                payload = {
                    'schema_version': 2,
                    'source_id': 'probe:edge-detect',
                    'source_session_id': source_session_id,
                    'batch_id': str(uuid4()),
                    'batch_sequence': batch_sequence,
                    'alerts': [],
                    'flows': [{
                        'sample_id': str(uuid4()),
                        'occurred_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                        'src_ip': src_ip,
                        'dst_ip': dst_ip,
                        'src_port': sport,
                        'dst_port': dport,
                        'network_protocol': protocol,
                        'bytes': packet_bytes,
                        'packets': 1,
                        'flow_count': 0,
                    }],
                }
                try:
                    response = requests.post(
                        f'{server_url}/api/probe/push',
                        headers={'X-Probe-Token': probe_token},
                        json=payload,
                        timeout=3,
                    )
                    response.raise_for_status()
                except requests.RequestException as exc:
                    print('Push failed:', type(exc).__name__)

            if result['is_attack']:
                alert_count[0] += 1
                print('  [%4d] local-model %-8s | %s:%s -> %s:%s | %.0f%% (not sent as incident)' % (
                    packet_count[0], result['class_name'],
                    src_ip, sport, dst_ip, dport, result['confidence'] * 100))
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        print(f'\nStopped. {packet_count[0]} packets, {alert_count[0]} local model flags.')


def main():
    parser = argparse.ArgumentParser(description='IoT IDS Edge Detection')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--live', action='store_true', help='Live capture mode')
    mode.add_argument('--demo', action='store_true', help='Run the explicit mock-data demonstration')
    parser.add_argument('--server', help='Management server URL (e.g. http://192.168.0.100:5000)')
    parser.add_argument('--probe-token', default=os.getenv('IOT_IDS_PROBE_TOKEN', ''), help='Probe credential')
    args = parser.parse_args()

    engine = InferenceEngine(MODEL_PATH)
    extractor = FeatureExtractor(SCALER_PATH)

    if args.live:
        if args.server and not args.probe_token:
            raise SystemExit('Missing probe credential: set IOT_IDS_PROBE_TOKEN or pass --probe-token')
        detect_live(engine, extractor, args.server, args.probe_token)
    else:
        detect_demo(engine, extractor)


if __name__ == '__main__':
    main()
