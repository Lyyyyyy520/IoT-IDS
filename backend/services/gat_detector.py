"""
Real-time GAT detection engine — ties capture -> feature extraction -> GAT.

Flow:
    packets -> group into bidirectional flows (5-tuple)
            -> extract 72 CICIDS features per flow
            -> batch flows (KNN graph within batch) -> GAT inference
            -> per-flow prediction (Normal / Attack)

Used by both the backend (Windows) and the Raspberry Pi edge (via the same
backend/models + backend/services code paths).
"""
import os
from typing import List, Optional

import numpy as np

from services.cicids_feature_extract import FlowFeatureExtractor, FEATURE_DIM
from models.gat_inference import GATInferenceEngine


class RealTimeGATDetector:
    def __init__(self, model_path: Optional[str] = None, scaler_path: Optional[str] = None,
                 batch_size: int = 32, k: int = 8):
        self.batch_size = batch_size
        self.k = k
        # buffer of (features, flow_id) waiting to be batched
        self._buffer: List[tuple] = []
        if model_path is None:
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            model_path = os.path.join(here, 'data', 'best_model_gat_bin.onnx')
            scaler_path = os.path.join(here, 'data', 'scaler_gat_bin.pkl')
        self.engine = GATInferenceEngine(model_path, scaler_path, k=k)

    @property
    def model_loaded(self) -> bool:
        return self.engine.model_loaded

    def detect_flow(self, features: np.ndarray, flow_id: object = None):
        """
        Queue one flow's 72-dim features. When `batch_size` flows accumulate,
        they are detected as a batch and results returned.

        Returns a result dict (or None if still buffering).
        """
        self._buffer.append((np.asarray(features, dtype=np.float32), flow_id))
        if len(self._buffer) >= self.batch_size:
            return self._flush()

    def _flush(self):
        if not self._buffer:
            return None
        feats = np.vstack([f for f, _ in self._buffer])
        ids = [i for _, i in self._buffer]
        results = self.engine.predict_batch(feats)
        out = []
        for flow_id, r in zip(ids, results):
            r = dict(r)
            r['flow_id'] = flow_id
            out.append(r)
        self._buffer.clear()
        return out

    def flush(self):
        """Force-detect whatever is buffered."""
        if not self._buffer:
            return []
        # pad to >=2 for KNN
        while len(self._buffer) < 2:
            self._buffer.append(self._buffer[-1])
        return self._flush() or []

    # ---- convenience: packets -> flow -> detect -------------------------- #
    @staticmethod
    def packets_to_flow(packets, proto: int = 6) -> Optional[np.ndarray]:
        ext = FlowFeatureExtractor(proto)
        for ts, is_fwd, length, flags in packets:
            ext.add_packet(ts, is_fwd, length, flags)
        return ext.extract()


if __name__ == '__main__':
    # End-to-end smoke test: synthetic normal + attack-like flows
    det = RealTimeGATDetector()
    print(f'Model loaded: {det.model_loaded}')

    def make_flow(n_pkts, fwd_ratio, base_len, flags='A', dur_step=0.05):
        ext = FlowFeatureExtractor(proto=6)
        for i in range(n_pkts):
            is_fwd = (i % 10) < (fwd_ratio * 10)
            ext.add_packet(i * dur_step, is_fwd, base_len + (i % 7) * 8, flags)
        return ext.extract()

    # normal flow: moderate, ACK flags
    f_normal = make_flow(30, 0.6, 60, 'A')
    # attack-like flow: many SYN (scan), small packets
    f_attack = make_flow(30, 0.95, 30, 'S')

    res = det.detect_flow(f_normal, 'flow-normal')
    res = det.detect_flow(f_attack, 'flow-attack') or res
    if res:
        for r in res:
            print(f"  {r['flow_id']:12s} -> {r['class_name']:6s} conf={r['confidence']:.3f} risk={r['risk_level']}")
    else:
        print('  (buffering, flush...')
        for r in det.flush():
            print(f"  {r['flow_id']:12s} -> {r['class_name']:6s} conf={r['confidence']:.3f}")
