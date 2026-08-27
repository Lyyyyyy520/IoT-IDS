"""
从 CICIoT2023 parquet 抽样一个平衡子集（含 IP + 标签），用于设备图 GNN。

核心：Benign vs Mirai（僵尸网络二分类），附少量 DDoS/DoS/Recon 供多分类演示。

策略：
  - Benign 全保留（稀缺）
  - Mirai 三类：greip 抽样到 50 万，greeth/udpplain 全保留
  - DDoS-SYN / DoS-TCP / Recon-PortScan 各抽样 20 万

用法:
    python training/sample_ciciot.py
"""
import os
import glob

import pyarrow.parquet as pq

CACHE_DIR = 'd:/hf_cache/datasets--Lystea--CICIOT2023-PARQUET/snapshots'
OUT_DIR = 'd:/Project/iot-ids/training/data/ciciot2023'
OUT = os.path.join(OUT_DIR, 'flows_sample.csv')
os.makedirs(OUT_DIR, exist_ok=True)

KEY_COLS = ['src_ip', 'dst_ip', 'src_port', 'dst_port', 'protocol',
            'packets', 'bytes', 'tcp_flags', 'ts_start', 'ts_end', 'label_class']

# 类别 -> 抽样上限（None 表示全保留）
SAMPLE_PLAN = {
    'Benign_Final': None,          # 全保留 35.8 万
    'Mirai-greip_flood': 500000,   # 抽样到 50 万
    'Mirai-greeth_flood': None,    # 全保留 17.5 万
    'Mirai-udpplain': None,        # 全保留 14 万
    'DDoS-SYN_Flood': 200000,      # 抽样 20 万
    'DoS-TCP_Flood': 200000,       # 抽样 20 万
    'Recon-PortScan': None,        # 全保留 19.3 万
}

all_files = sorted(glob.glob(os.path.join(CACHE_DIR, '*', '*.parquet')))

# 按计划分组文件
from collections import defaultdict
groups = defaultdict(list)
for f in all_files:
    cls = os.path.basename(f).split('__')[0]
    if cls in SAMPLE_PLAN:
        groups[cls].append(f)

print('抽样计划:', flush=True)
for cls, files in groups.items():
    cap = SAMPLE_PLAN[cls]
    total = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
    keep = total if cap is None else min(cap, total)
    print(f'  {cls:<24} {len(files):>3}文件 {total:>12,}行 -> 保留 {keep:>12,}行', flush=True)

if os.path.exists(OUT):
    os.remove(OUT)

first = True
total_kept = 0

for cls in SAMPLE_PLAN:
    files = groups.get(cls, [])
    if not files:
        continue
    cap = SAMPLE_PLAN[cls]
    cls_total = sum(pq.ParquetFile(f).metadata.num_rows for f in files)

    # 计算每个文件抽样比例
    frac = None
    if cap is not None and cls_total > cap:
        frac = cap / cls_total

    kept_cls = 0
    for f in files:
        try:
            df = pq.read_table(f, columns=KEY_COLS).to_pandas()
            if frac is not None:
                df = df.sample(frac=frac, random_state=42)
            df.to_csv(OUT, index=False, mode='a', header=first)
            first = False
            kept_cls += len(df)
            total_kept += len(df)
        except Exception as e:
            print(f'  [跳过] {os.path.basename(f)}: {str(e)[:50]}', flush=True)

    print(f'  已写 {cls}: {kept_cls:,} 行 (累计 {total_kept:,})', flush=True)

print(f'\n完成！总 {total_kept:,} 行 -> {OUT}', flush=True)
