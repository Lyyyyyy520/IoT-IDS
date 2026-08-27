"""
N-BaIoT Preprocessing → 3-class (Normal / Mirai / Gafgyt)

Source: 9 IoT devices authentically infected by Mirai & Gafgyt (BASHLITE).
Each device has 11 files: 1 benign + 5 mirai variants + 5 gafgyt variants.
Raw features: 115 statistical flow features (N-BaIoT standard).

Class mapping (from filename):
    benign.*           -> 0  Normal
    mirai.*            -> 1  Mirai
    gafgyt.*           -> 2  Gafgyt

Output (training/data/processed_nbaiot/):
    train.csv / test.csv     features + label column
    scaler.pkl / feature_names.pkl / label_names.pkl / class_weights.pkl

Usage:
    python preprocess_nbaiot.py --samples_per_class 30000 --test_ratio 0.2
"""
import os
import re
import argparse
import pickle

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data', 'n-baiot')
OUT_DIR = os.path.join(os.path.dirname(__file__), 'data', 'processed_nbaiot')

LABEL_NAMES = ['Normal', 'Mirai', 'Gafgyt']
DATA_FILE_RE = re.compile(r'^\d+\.(benign|mirai|gafgyt)\.')  # e.g. "1.mirai.scan.csv"


def label_from_filename(fname: str):
    """Return class id from filename, or None if it's a metadata file."""
    base = os.path.basename(fname).lower()
    if 'benign' in base:
        return 0
    if 'mirai' in base:
        return 1
    if 'gafgyt' in base:
        return 2
    return None


def collect_data_files():
    """Return list of (path, label) for every N-BaIoT data CSV."""
    files = []
    for f in sorted(os.listdir(DATA_DIR)):
        if not f.endswith('.csv'):
            continue
        if not DATA_FILE_RE.match(f):
            continue  # skip metadata (features.csv / data_summary.csv / ...)
        label = label_from_filename(f)
        files.append((os.path.join(DATA_DIR, f), label))
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples_per_class', type=int, default=30000,
                        help='target samples per class (total = 3x this)')
    parser.add_argument('--test_ratio', type=float, default=0.2)
    args = parser.parse_args()

    files = collect_data_files()
    print(f'Found {len(files)} data files')

    # Group files by class
    by_class = {0: [], 1: [], 2: []}
    for path, label in files:
        by_class[label].append(path)
    for lbl in (0, 1, 2):
        print(f'  {LABEL_NAMES[lbl]}: {len(by_class[lbl])} files')

    # Sample per file: split the per-class target evenly across its files.
    parts = []
    for lbl in (0, 1, 2):
        n_files = len(by_class[lbl])
        per_file = max(1, args.samples_per_class // n_files)
        print(f'[Load] {LABEL_NAMES[lbl]} (up to {per_file}/file)...')
        for path in by_class[lbl]:
            df = pd.read_csv(path, nrows=per_file)
            df['label'] = lbl
            parts.append(df)

    data = pd.concat(parts, ignore_index=True)
    print(f'Total loaded: {len(data):,} rows')

    # Balance: cap each class at samples_per_class (benign files may be smaller)
    balanced = []
    for lbl in (0, 1, 2):
        sub = data[data['label'] == lbl]
        if len(sub) > args.samples_per_class:
            sub = sub.sample(n=args.samples_per_class, random_state=42)
        balanced.append(sub)
    data = pd.concat(balanced).sample(frac=1, random_state=42).reset_index(drop=True)
    print(f'After balancing: {len(data):,} rows')

    feature_cols = [c for c in data.columns if c != 'label']
    X = data[feature_cols].astype(np.float32)
    y = data['label'].astype(np.int64)

    # Drop constant columns (N-BaIoT may have a few zero-variance cols)
    non_const = X.columns[X.var() > 1e-8]
    dropped = len(feature_cols) - len(non_const)
    X = X[non_const]
    if dropped:
        print(f'Dropped {dropped} constant columns')
    feature_cols = list(non_const)

    # Standardize
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    X_train, X_test, y_train, y_test = train_test_split(
        X_scaled, y, test_size=args.test_ratio, random_state=42, stratify=y
    )

    for i, n in enumerate(LABEL_NAMES):
        print(f'  {n}: train {sum(y_train == i):,}  test {sum(y_test == i):,}')

    # Class weights (inverse frequency, normalised)
    counts = pd.Series(y_train).value_counts().sort_index().values
    w = 1.0 / counts
    w = w / w.sum() * len(w)

    os.makedirs(OUT_DIR, exist_ok=True)
    train_df = pd.DataFrame(X_train, columns=feature_cols)
    train_df['label'] = y_train.values
    test_df = pd.DataFrame(X_test, columns=feature_cols)
    test_df['label'] = y_test.values
    train_df.to_csv(os.path.join(OUT_DIR, 'train.csv'), index=False)
    test_df.to_csv(os.path.join(OUT_DIR, 'test.csv'), index=False)
    pickle.dump(scaler, open(os.path.join(OUT_DIR, 'scaler.pkl'), 'wb'))
    pickle.dump(feature_cols, open(os.path.join(OUT_DIR, 'feature_names.pkl'), 'wb'))
    pickle.dump(LABEL_NAMES, open(os.path.join(OUT_DIR, 'label_names.pkl'), 'wb'))
    pickle.dump(w.astype(np.float32), open(os.path.join(OUT_DIR, 'class_weights.pkl'), 'wb'))

    print(f'\nDone! Saved -> {OUT_DIR}/  feature dim: {len(feature_cols)}')


if __name__ == '__main__':
    main()
