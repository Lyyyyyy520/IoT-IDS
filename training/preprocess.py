"""
BoT-IoT v4：只对Normal类SMOTE + 攻击类采样 + 类别权重
"""
import os, numpy as np, pandas as pd, pickle
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from imblearn.over_sampling import SMOTE

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
OUT_DIR = os.path.join(DATA_DIR, 'processed')
LABEL_NAMES = ['Normal', 'Mirai', 'Gafgyt', 'Other']


def load():
    print('[1/4] Load BoT-IoT...')
    root = os.path.join(DATA_DIR, 'bot-iot', '5%', 'All features')
    files = sorted([f for f in os.listdir(root) if f.endswith('.csv')])
    dfs = []
    for f in files:
        df = pd.read_csv(os.path.join(root, f), low_memory=False)
        df['label'] = df.apply(lambda r: 0 if int(r.get('attack', 0)) == 0 else
                         1 if 'ddos' in str(r.get('category', '')).lower() else
                         2 if 'dos' in str(r.get('category', '')).lower() else 3, axis=1)
        dfs.append(df)
    data = pd.concat(dfs, ignore_index=True).dropna(subset=['label'])
    data['label'] = data['label'].astype(int)
    for i, n in enumerate(LABEL_NAMES):
        print(f'  {n}: {sum(data["label"]==i):,}')
    return data


def clean_filter(df):
    print('[2/4] Clean + Filter...')
    df = df.drop_duplicates().replace([np.inf, -np.inf], np.nan)
    num = df.select_dtypes(include=[np.number]).columns.tolist()
    if 'label' not in num: num.append('label')
    df = df[num].dropna()
    print(f'  After clean: {len(df):,}')

    X = df.drop(columns=['label'])
    X = X[X.columns[X.var() > 0.001]]
    corr = X.corr().abs()
    up = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
    drop = [c for c in up.columns if any(up[c] > 0.9)]
    X = X.drop(columns=drop)
    kws = ['pkts','bytes','dur','rate','mean','stddev','proto','sport','dport',
           'sbytes','dbytes','spkts','dpkts','srate','drate','ltime','min','max','sum','flgs','state']
    keep = [c for c in X.columns if any(k in c.lower() for k in kws)]
    if len(keep) >= 10: X = X[keep]
    print(f'  Features: {X.shape[1]} dims')
    return pd.concat([X, df['label'].reset_index(drop=True)], axis=1), X.columns.tolist()


def split_save(df, feats):
    print('[3/4] Split + SMOTE(Normal only)...')
    # 精简版：攻击类采样到10万，Normal保留全部
    max_per = 100000
    parts = []
    for lbl in sorted(df['label'].unique()):
        sub = df[df['label'] == lbl]
        if lbl > 0 and len(sub) > max_per:
            sub = sub.sample(n=max_per, random_state=42)
        parts.append(sub)
    df = pd.concat(parts).sample(frac=1, random_state=42)

    X = df[feats].astype(float)
    y = df['label'].astype(int)
    sc = StandardScaler()
    X_s = pd.DataFrame(sc.fit_transform(X), columns=feats)
    Xt, Xv, yt, yv = train_test_split(X_s, y, test_size=0.2, random_state=42, stratify=y)

    for i, n in enumerate(LABEL_NAMES):
        print(f'  {n}: train {sum(yt==i):,}  test {sum(yv==i):,}')

    cnts = yt.value_counts().sort_index().values
    w = (1.0 / cnts)
    w = w / w.sum() * len(w)
    print(f'  Class weights: {w.round(4)}')

    os.makedirs(OUT_DIR, exist_ok=True)
    Xt.assign(label=yt.values).to_csv(f'{OUT_DIR}/train.csv', index=False)
    Xv.assign(label=yv.values).to_csv(f'{OUT_DIR}/test.csv', index=False)
    pickle.dump(sc, open(f'{OUT_DIR}/scaler.pkl', 'wb'))
    pickle.dump(feats, open(f'{OUT_DIR}/feature_names.pkl', 'wb'))
    pickle.dump(LABEL_NAMES, open(f'{OUT_DIR}/label_names.pkl', 'wb'))
    pickle.dump(w.astype(np.float32), open(f'{OUT_DIR}/class_weights.pkl', 'wb'))
    print(f'[4/4] Saved -> {OUT_DIR}/  dims: {len(feats)}')


def main():
    df = load()
    df, feats = clean_filter(df)
    split_save(df, feats)
    print('\nPreprocess done!')


if __name__ == '__main__':
    main()
