"""
下载 TON-IoT 数据集到 D 盘（避开满的 C 盘），并导出成带 IP 的 CSV。

用法:
    python training/download_toniot.py
"""
import os

# 关键：把 Hugging Face 缓存设到 D 盘（C 盘已满）
os.environ['HF_HOME'] = 'd:/hf_cache'
os.environ['HF_HUB_CACHE'] = 'd:/hf_cache'

import pandas as pd
from datasets import load_dataset

OUT_DIR = 'd:/Project/iot-ids/training/data/ton-iot'
os.makedirs(OUT_DIR, exist_ok=True)

print('下载 TON-IoT (codymlewis/TON_IoT_network) ...')
ds = load_dataset('codymlewis/TON_IoT_network')

for split in ds.keys():
    df = ds[split].to_pandas()
    path = os.path.join(OUT_DIR, f'{split}.csv')
    df.to_csv(path, index=False)
    print(f'  {split}: {len(df):,} 行 -> {path}')

    # 打印关键列信息
    if split == 'train':
        print(f'\n  列数: {df.shape[1]}')
        ip_cols = [c for c in df.columns if 'ip' in c.lower()]
        label_cols = [c for c in df.columns if c in ('label', 'type')]
        print(f'  IP列: {ip_cols}')
        print(f'  标签列: {label_cols}')
        if 'type' in df.columns:
            print(f'  攻击类型分布:\n{df["type"].value_counts().to_string()}')

print('\n完成！数据在 d:/Project/iot-ids/training/data/ton-iot/')
