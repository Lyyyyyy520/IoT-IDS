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

# Add parent to path for backend imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from models.inference import InferenceEngine
from services.feature_extract import FeatureExtractor

MODEL_PATH = os.path.join(os.path.dirname(__file__), '..', 'backend', 'data', 'best_model.onnx')
SCALER_PATH = os.path.join(os.path.dirname(__file__), '..', 'backend', 'data', 'scaler.pkl')


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


def main():
    parser = argparse.ArgumentParser(description='IoT IDS Edge Detection')
    parser.add_argument('--pcap', help='Path to PCAP file for analysis')
    parser.add_argument('--live', action='store_true', help='Live capture mode')
    args = parser.parse_args()

    engine = InferenceEngine(MODEL_PATH)
    extractor = FeatureExtractor(SCALER_PATH)

    if args.pcap:
        detect_pcap(engine, extractor, args.pcap)
    elif args.live:
        print('Live capture mode — not yet implemented (requires Scapy + sudo)')
        print('Falling back to demo mode...')
        detect_demo(engine, extractor)
    else:
        detect_demo(engine, extractor)


if __name__ == '__main__':
    main()
