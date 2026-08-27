"""
从 HF 缓存读取 CICIoT2023 parquet（含 IP + 标签），增量合并成 CSV。

特点：边读边写（不积压内存），每处理 20 个文件打印进度，中断不丢已写数据。

用法:
    python training/convert_ciciot.py
"""
import os
import glob

import pyarrow.parquet as pq

CACHE_DIR = 'd:/hf_cache/datasets--Lystea--CICIOT2023-PARQUET/snapshots'
OUT_DIR = 'd:/Project/iot-ids/training/data/ciciot2023'
OUT = os.path.join(OUT_DIR, 'flows.csv')
os.makedirs(OUT_DIR, exist_ok=True)

# 设备图 GNN 需要的关键列
KEY_COLS = ['src_ip', 'dst_ip', 'src_port', 'dst_port', 'protocol',
            'packets', 'bytes', 'tcp_flags', 'ts_start', 'ts_end', 'label_class']

files = sorted(glob.glob(os.path.join(CACHE_DIR, '*', '*.parquet')))
print(f'找到 {len(files)} 个 parquet 文件', flush=True)

if os.path.exists(OUT):
    os.remove(OUT)  # 从头写

total_rows = 0
written = 0
skipped = 0
first = True

for i, f in enumerate(files):
    name = os.path.basename(f)
    try:
        t = pq.read_table(f, columns=KEY_COLS)
        df = t.to_pandas()
        if 'label_class' not in df.columns or 'src_ip' not in df.columns:
            skipped += 1
            print(f'  [跳过 缺列] {name}', flush=True)
            continue
        df.to_csv(OUT, index=False, mode='a', header=first)
        first = False
        written += 1
        total_rows += len(df)
    except Exception as e:
        skipped += 1
        print(f'  [跳过 读失败] {name} - {str(e)[:50]}', flush=True)

    if (i + 1) % 20 == 0:
        print(f'  进度 {i+1}/{len(files)}  已写 {written} 文件  {total_rows:,} 行', flush=True)

print(f'\n完成：成功 {written} 文件，跳过 {skipped}，总 {total_rows:,} 行', flush=True)
print(f'输出: {OUT}', flush=True)
