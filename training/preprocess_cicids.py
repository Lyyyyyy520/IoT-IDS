"""
CICIDS2017 Preprocessing → Binary (Normal / Attack)

Input:  training/data/cicids2017/cic_full_cleaned.csv
        (81 cols = 77 CICFlowMeter flow features + 4 label columns)

Binary label (from `Label` column):
    "BENIGN" -> 0  Normal
    others   -> 1  Attack

Output (training/data/processed_cicids/):
    train.csv / test.csv / scaler.pkl / feature_names.pkl / label_names.pkl

Usage:
    python preprocess_cicids.py --samples_per_class 150000
"""
import os
import argparse
import pickle

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split

CSV_PATH = os.path.join(os.path.dirname(__file__), 'data', 'cicids2017', 'cic_full_cleaned.csv')
OUT_DIR = os.path.join(os.path.dirname(__file__), 'data', 'processed_cicids')

LABEL_COLS = ['Label_Flow', 'Label', 'Label_Code', 'Label_Bin']
LABEL_NAMES = ['Normal', 'Attack']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples_per_class', type=int, default=150000)
    parser.add_argument('--test_ratio', type=float, default=0.2)
    args = parser.parse_args()

    feature_cols = None
    X_parts, y_parts = [], []

    print(f'Reading {CSV_PATH} ...')
    reader = pd.read_csv(CSV_PATH, chunksize=500000, low_memory=False)
    for i, chunk in enumerate(reader):
        if feature_cols is None:
            feature_cols = [c for c in chunk.columns if c not in LABEL_COLS]
            print(f'Feature cols: {len(feature_cols)}')

        # Features -> numeric, label -> binary
        X = chunk[feature_cols].apply(pd.to_numeric, errors='coerce')
        y = (chunk['Label'].astype(str).str.strip() != 'BENIGN').astype(np.int8)

        # Clean: replace inf -> nan, drop rows with any nan
        X = X.replace([np.inf, -np.inf], np.nan)
        mask = ~X.isna().any(axis=1)
        X, y = X[mask].values.astype(np.float32), y[mask].values

        X_parts.append(X)
        y_parts.append(y)
        print(f'  chunk {i+1}: {X.shape[0]:,} rows kept')

    X = np.vstack(X_parts)
    y = np.concatenate(y_parts)
    print(f'Total cleaned: {X.shape[0]:,} rows')

    # Balance classes
    balanced_X, balanced_y = [], []
    for lbl in (0, 1):
        idx = np.where(y == lbl)[0]
        print(f'  {LABEL_NAMES[lbl]}: {len(idx):,} rows')
        if len(idx) > args.samples_per_class:
            idx = np.random.choice(idx, args.samples_per_class, replace=False)
        balanced_X.append(X[idx])
        balanced_y.append(y[idx])
    X = np.vstack(balanced_X)
    y = np.concatenate(balanced_y)
    print(f'After balancing: {X.shape[0]:,} rows')

    # Drop constant columns
    non_const = np.var(X, axis=0) > 1e-8
    n_dropped = (~non_const).sum()
    X = X[:, non_const]
    feature_cols = [c for c, keep in zip(feature_cols, non_const) if keep]
    if n_dropped:
        print(f'Dropped {n_dropped} constant columns -> {len(feature_cols)} features')

    # Shuffle
    perm = np.random.permutation(len(y))
    X, y = X[perm], y[perm]

    # Standardize
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X).astype(np.float32)

    X_train, X_test, y_train, y_test = train_test_split(
        X_scaled, y, test_size=args.test_ratio, random_state=42, stratify=y
    )
    for i, n in enumerate(LABEL_NAMES):
        print(f'  {n}: train {sum(y_train == i):,}  test {sum(y_test == i):,}')

    # Class weights
    counts = np.bincount(y_train, minlength=2)
    w = 1.0 / counts
    w = w / w.sum() * len(w)

    os.makedirs(OUT_DIR, exist_ok=True)
    train_df = pd.DataFrame(X_train, columns=feature_cols)
    train_df['label'] = y_train
    test_df = pd.DataFrame(X_test, columns=feature_cols)
    test_df['label'] = y_test
    train_df.to_csv(os.path.join(OUT_DIR, 'train.csv'), index=False)
    test_df.to_csv(os.path.join(OUT_DIR, 'test.csv'), index=False)
    pickle.dump(scaler, open(os.path.join(OUT_DIR, 'scaler.pkl'), 'wb'))
    pickle.dump(feature_cols, open(os.path.join(OUT_DIR, 'feature_names.pkl'), 'wb'))
    pickle.dump(LABEL_NAMES, open(os.path.join(OUT_DIR, 'label_names.pkl'), 'wb'))
    pickle.dump(w.astype(np.float32), open(os.path.join(OUT_DIR, 'class_weights.pkl'), 'wb'))

    print(f'\nDone! Saved -> {OUT_DIR}/  feature dim: {len(feature_cols)}')


if __name__ == '__main__':
    main()
