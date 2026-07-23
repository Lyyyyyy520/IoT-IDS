"""
Raspberry Pi Edge Detection Program

Runs ONNX inference locally with optional Scapy packet capture.
Designed for Raspberry Pi 4B (4GB) with Raspberry Pi OS Bullseye 64-bit.

Usage:
  python edge_detect.py                      # Run with mock data demo
  python edge_detect.py --pcap test.pcap     # Analyze a PCAP file
  python edge_detect.py --live               # Live capture (requires sudo + Scapy)
"""
import os
import sys
import time
import argparse
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


def detect_pcap(engine: InferenceEngine, extractor: FeatureExtractor, pcap_path: str):
    """Analyze a PCAP file."""
    print(f'\nAnalyzing PCAP: {pcap_path}')
    features_list = extractor.extract_from_pcap(pcap_path)
    print(f'Extracted {len(features_list)} flow records')

    attack_count = 0
    for feat in features_list:
        result = engine.predict(feat)
        if result['is_attack']:
            attack_count += 1
            print(f'  ⚠ {result["class_name"]} — Confidence: {result["confidence"]:.2%}')

    print(f'\nSummary: {attack_count}/{len(features_list)} flows flagged as attack')


def detect_live(engine: InferenceEngine, extractor: FeatureExtractor, server_url: str = None):
    """Real-time packet capture using tcpdump pipe and ONNX detection."""
    import subprocess, re, threading, requests, json

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

    # Use tcpdump to capture packets, parse with regex
    proc = subprocess.Popen(
        ['sudo', 'tcpdump', '-i', 'eth0', '-l', '-n', '-tt', 'ip'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=0
    )

    ip_re = re.compile(r'(\d+\.\d+\.\d+\.\d+)\.(\d+)\s*>\s*(\d+\.\d+\.\d+\.\d+)\.(\d+)')

    print('Capture started. Press Ctrl+C to stop.\n')
    try:
        for line in iter(proc.stdout.readline, ''):
            m = ip_re.search(line)
            if not m:
                continue
            packet_count[0] += 1
            src_ip, sport, dst_ip, dport = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
            proto = 1 if 'TCP' in line.upper() else 2 if 'UDP' in line.upper() else 0
            if proto == 0:
                continue

            flow_data = {
                'protocol_type': proto, 'src_port': sport, 'dst_port': dport,
                'min_packet_length': 100, 'flow_duration': 0.01,
                'flow_bytes_per_sec': 10000, 'flow_packets_per_sec': 100,
                'syn_count': 1, 'ack_count': 1,
            }
            feat = extractor.extract_from_flow(flow_data)
            result = engine.predict(feat)

            # Push ALL traffic to backend as flow logs
            if server_url and packet_count[0] % 5 == 0:  # Only push every 5th packet to avoid flooding
                try:
                    r = requests.post(f'{server_url}/api/probe/push', json={
                        'probe_name': 'Pi-Probe',
                        'alerts': ([{
                            'risk_level': result['risk_level'],
                            'attack_type': result['class_name'],
                            'src_ip': src_ip, 'dst_ip': dst_ip,
                            'src_port': sport, 'dst_port': dport,
                            'protocol': 'TCP' if proto == 1 else 'UDP',
                            'confidence': result['confidence'],
                            'description': '[Pi] ' + result['class_name'] + ' real-time',
                        }] if result['is_attack'] else []),
                        'flows': [{
                            'src_ip': src_ip, 'dst_ip': dst_ip,
                            'src_port': sport, 'dst_port': dport,
                            'protocol': 'TCP' if proto == 1 else 'UDP',
                            'length': 100, 'flags': '',
                            'source': '真实',
                        }]
                    }, timeout=3)
                except Exception as e:
                    print('Push err:', e)

            if result['is_attack']:
                alert_count[0] += 1
                print('  [%4d] %s %-8s | %s:%s -> %s:%s | %.0f%%' % (
                    packet_count[0], result['risk_level'], result['class_name'],
                    src_ip, sport, dst_ip, dport, result['confidence']*100))
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        print(f'\nStopped. {packet_count[0]} packets, {alert_count[0]} alerts.')


def main():
    parser = argparse.ArgumentParser(description='IoT IDS Edge Detection')
    parser.add_argument('--pcap', help='Path to PCAP file for analysis')
    parser.add_argument('--live', action='store_true', help='Live capture mode')
    parser.add_argument('--server', help='Management server URL (e.g. http://192.168.0.100:5000)')
    args = parser.parse_args()

    engine = InferenceEngine(MODEL_PATH)
    extractor = FeatureExtractor(SCALER_PATH)

    if args.pcap:
        detect_pcap(engine, extractor, args.pcap)
    elif args.live:
        detect_live(engine, extractor, args.server)
    else:
        detect_demo(engine, extractor)


if __name__ == '__main__':
    main()
