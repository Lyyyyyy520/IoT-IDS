"""
CICFlowMeter-style feature extraction for real-time detection.

Computes the 72 CICIDS2017 flow features (in the SAME order the binary GAT
model was trained on) from a stream of packets, so the model can run on live
traffic captured by scapy/tcpdump.

A "flow" is a bidirectional 5-tuple (src_ip, src_port, dst_ip, dst_port, proto).
Forward = packets from src->dst, backward = dst->src.

Usage:
    from services.cicids_feature_extract import FlowFeatureExtractor
    ext = FlowFeatureExtractor()
    ext.add_packet(ts, src, sport, dst, dport, proto, length, flags)
    ...
    vec = ext.extract()   # 72-dim numpy array, or None if flow too short
"""
import numpy as np

# The 72 feature names in the exact training order (from feature_names.pkl).
FEATURE_NAMES = [
    'Protocol', 'Flow Duration',
    'Total Fwd Packet', 'Total Bwd packets',
    'Total Length of Fwd Packet', 'Total Length of Bwd Packet',
    'Fwd Packet Length Max', 'Fwd Packet Length Min', 'Fwd Packet Length Mean', 'Fwd Packet Length Std',
    'Bwd Packet Length Max', 'Bwd Packet Length Min', 'Bwd Packet Length Mean', 'Bwd Packet Length Std',
    'Flow Bytes/s', 'Flow Packets/s',
    'Flow IAT Mean', 'Flow IAT Std', 'Flow IAT Max', 'Flow IAT Min',
    'Fwd IAT Total', 'Fwd IAT Mean', 'Fwd IAT Std', 'Fwd IAT Max', 'Fwd IAT Min',
    'Bwd IAT Total', 'Bwd IAT Mean', 'Bwd IAT Std', 'Bwd IAT Max', 'Bwd IAT Min',
    'Fwd PSH Flags', 'Fwd URG Flags',
    'Fwd Header Length', 'Bwd Header Length',
    'Fwd Packets/s', 'Bwd Packets/s',
    'Packet Length Min', 'Packet Length Max', 'Packet Length Mean', 'Packet Length Std', 'Packet Length Variance',
    'FIN Flag Count', 'SYN Flag Count', 'RST Flag Count', 'PSH Flag Count', 'ACK Flag Count',
    'URG Flag Count', 'CWR Flag Count', 'ECE Flag Count',
    'Down/Up Ratio', 'Average Packet Size',
    'Fwd Segment Size Avg', 'Bwd Segment Size Avg',
    'Bwd Bytes/Bulk Avg', 'Bwd Packet/Bulk Avg', 'Bwd Bulk Rate Avg',
    'Subflow Fwd Packets', 'Subflow Fwd Bytes', 'Subflow Bwd Packets', 'Subflow Bwd Bytes',
    'FWD Init Win Bytes', 'Bwd Init Win Bytes', 'Fwd Act Data Pkts', 'Fwd Seg Size Min',
    'Active Mean', 'Active Std', 'Active Max', 'Active Min',
    'Idle Mean', 'Idle Std', 'Idle Max', 'Idle Min',
]

FEATURE_DIM = len(FEATURE_NAMES)  # 72

_FLAG_MAP = {
    'F': 'FIN', 'S': 'SYN', 'R': 'RST', 'P': 'PSH', 'A': 'ACK',
    'U': 'URG', 'C': 'CWR', 'E': 'ECE',
}

_IDLE_THRESHOLD = 1.0  # seconds — gap larger than this marks an idle period


def _stats(values):
    """Return (mean, std, max, min) of a list; empty -> (0,0,0,0)."""
    if len(values) == 0:
        return 0.0, 0.0, 0.0, 0.0
    a = np.asarray(values, dtype=np.float64)
    return float(a.mean()), float(a.std()), float(a.max()), float(a.min())


class FlowFeatureExtractor:
    """Accumulates packets of one flow and produces the 72-dim feature vector."""

    def __init__(self, proto: int = 6):
        self.proto = proto
        self.ts = []        # timestamps
        self.fwd = []       # booleans: True=forward
        self.length = []    # packet lengths
        self.flags = []     # set of flag chars per packet

    # -- accumulation ------------------------------------------------------ #
    def add_packet(self, ts: float, is_forward: bool, length: int, flags: str = ''):
        self.ts.append(float(ts))
        self.fwd.append(bool(is_forward))
        self.length.append(int(length))
        self.flags.append(set(flags.upper()))

    # -- extraction -------------------------------------------------------- #
    def extract(self) -> np.ndarray:
        n = len(self.ts)
        if n < 2:
            return None

        ts = np.asarray(self.ts, dtype=np.float64)
        length = np.asarray(self.length, dtype=np.float64)
        fwd_mask = np.asarray(self.fwd, dtype=bool)

        fwd_len = length[fwd_mask]
        bwd_len = length[~fwd_mask]
        n_fwd = int(fwd_mask.sum())
        n_bwd = int((~fwd_mask).sum())

        # duration / rates
        duration = float(ts.max() - ts.min())
        dur_safe = duration if duration > 0 else 1e-6
        total_len = float(length.sum())
        fwd_total_len = float(fwd_len.sum())
        bwd_total_len = float(bwd_len.sum())

        # inter-arrival times
        iat = np.diff(ts)
        fwd_ts = ts[fwd_mask]
        bwd_ts = ts[~fwd_mask]
        fwd_iat = np.diff(fwd_ts) if len(fwd_ts) > 1 else np.array([])
        bwd_iat = np.diff(bwd_ts) if len(bwd_ts) > 1 else np.array([])

        # flag counts
        flag_count = {f: 0 for f in _FLAG_MAP.values()}
        fwd_flag_count = {f: 0 for f in _FLAG_MAP.values()}
        for i, fs in enumerate(self.flags):
            for f in _FLAG_MAP:
                if f in fs:
                    flag_count[_FLAG_MAP[f]] += 1
                    if self.fwd[i]:
                        fwd_flag_count[_FLAG_MAP[f]] += 1

        # active / idle periods (gap > threshold = idle)
        active, idle = [], []
        cur = ts[0]
        for t in ts[1:]:
            gap = t - cur
            if gap <= _IDLE_THRESHOLD:
                active.append(gap)
            else:
                idle.append(gap)
            cur = t

        # ---- assemble in exact FEATURE_NAMES order ----------------------- #
        fwd_len_m, fwd_len_s, fwd_len_x, fwd_len_n = _stats(fwd_len)
        bwd_len_m, bwd_len_s, bwd_len_x, bwd_len_n = _stats(bwd_len)
        all_m, all_s, all_x, all_n = _stats(length)
        iat_m, iat_s, iat_x, iat_n = _stats(iat)
        fwd_iat_m, fwd_iat_s, fwd_iat_x, fwd_iat_n = _stats(fwd_iat)
        bwd_iat_m, bwd_iat_s, bwd_iat_x, bwd_iat_n = _stats(bwd_iat)
        act_m, act_s, act_x, act_n = _stats(active)
        idle_m, idle_s, idle_x, idle_n = _stats(idle)

        # down/up ratio: download(backward) / upload(forward)
        down_up = (bwd_total_len / fwd_total_len) if fwd_total_len > 0 else 0.0

        feats = [
            float(self.proto),                # Protocol
            duration,                         # Flow Duration
            n_fwd, n_bwd,                     # Total Fwd/Bwd Packet
            fwd_total_len, bwd_total_len,     # Total Length Fwd/Bwd
            fwd_len_x, fwd_len_n, fwd_len_m, fwd_len_s,   # Fwd len max/min/mean/std
            bwd_len_x, bwd_len_n, bwd_len_m, bwd_len_s,   # Bwd len max/min/mean/std
            total_len / dur_safe,             # Flow Bytes/s
            n / dur_safe,                     # Flow Packets/s
            iat_m, iat_s, iat_x, iat_n,       # Flow IAT
            float(fwd_iat.sum()), fwd_iat_m, fwd_iat_s, fwd_iat_x, fwd_iat_n,  # Fwd IAT
            float(bwd_iat.sum()), bwd_iat_m, bwd_iat_s, bwd_iat_x, bwd_iat_n,  # Bwd IAT
            fwd_flag_count['PSH'], fwd_flag_count['URG'],   # Fwd PSH/URG Flags
            float(fwd_len.sum() / n_fwd) if n_fwd else 0.0,  # Fwd Header Length (approx=avg len)
            float(bwd_len.sum() / n_bwd) if n_bwd else 0.0,  # Bwd Header Length
            n_fwd / dur_safe,                 # Fwd Packets/s
            n_bwd / dur_safe,                 # Bwd Packets/s
            all_n, all_x, all_m, all_s, float(np.var(length)),  # Packet length stats
            flag_count['FIN'], flag_count['SYN'], flag_count['RST'], flag_count['PSH'],
            flag_count['ACK'], flag_count['URG'], flag_count['CWR'], flag_count['ECE'],
            down_up,                          # Down/Up Ratio
            total_len / n,                    # Average Packet Size
            fwd_len_m, bwd_len_m,             # Fwd/Bwd Segment Size Avg (approx=mean len)
            bwd_len_m, bwd_len_m, bwd_len_m / dur_safe if dur_safe else 0.0,  # Bwd Bulk (approx)
            n_fwd, fwd_total_len, n_bwd, bwd_total_len,  # Subflow (approx=whole flow)
            0.0, 0.0, 0.0, fwd_len_n,         # Init Win / Act Data Pkts / Seg Size Min
            act_m, act_s, act_x, act_n,       # Active
            idle_m, idle_s, idle_x, idle_n,   # Idle
        ]
        vec = np.asarray(feats, dtype=np.float32)
        # guard against inf/nan
        vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
        return vec


def extract_features_from_packets(packets, proto: int = 6) -> np.ndarray:
    """
    Convenience: build one flow from a packet list and extract its features.

    packets: list of (timestamp, is_forward, length, flags)
    """
    ext = FlowFeatureExtractor(proto)
    for ts, is_fwd, length, flags in packets:
        ext.add_packet(ts, is_fwd, length, flags)
    return ext.extract()


if __name__ == '__main__':
    # Smoke test: synthetic forward/backward flow
    ext = FlowFeatureExtractor(proto=6)
    for i in range(20):
        ext.add_packet(i * 0.05, i % 3 != 0, 40 + (i % 5) * 10, 'A')
    vec = ext.extract()
    print(f'Feature dim: {vec.shape}')
    print(f'Non-zero: {(vec != 0).sum()}/{FEATURE_DIM}')
    print('Sample:', {FEATURE_NAMES[i]: round(float(vec[i]), 2) for i in [0, 1, 2, 3, 14, 42, 49]})
