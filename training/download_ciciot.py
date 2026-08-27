"""
下载 CICIoT2023 已处理版本（含 IP + 标签）到 D 盘，并导出 CSV。

数据源: Hugging Face 的 Lystea/CICIOT2023-PARQUET
  含列: src_ip, dst_ip, label_class + 流量特征

用法:
    python training/download_ciciot.py
"""
import os

# 关键：把 Hugging Face 缓存设到 D 盘（C 盘已满）
os.environ['HF_HOME'] = 'd:/hf_cache'
os.environ['HF_HUB_CACHE'] = 'd:/hf_cache'

from datasets import load_dataset

OUT_DIR = 'd:/Project/iot-ids/training/data/ciciot2023_parquet'
os.makedirs(OUT_DIR, exist_ok=True)

print('下载 CICIoT2023 已处理版 (Lystea/CICIOT2023-PARQUET) ...')
ds = load_dataset('Lystea/CICIOT2023-PARQUET')

for split in ds.keys():
    df = ds[split].to_pandas()
    path = os.path.join(OUT_DIR, f'{split}.csv')
    df.to_csv(path, index=False)
    print(f'  {split}: {len(df):,} 行 -> {path}')

    if split == 'train':
        print(f'\n  列数: {df.shape[1]}')
        ip_cols = [c for c in df.columns if 'ip' in c.lower()]
        print(f'  IP列: {ip_cols}')
        if 'label_class' in df.columns:
            print(f'  标签分布 (前15类):\n{df["label_class"].value_counts().head(15).to_string()}')

print('\n完成！数据在 d:/Project/iot-ids/training/data/ciciot2023_parquet/')
