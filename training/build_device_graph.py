"""
从 CICIoT2023 抽样数据建设备图（时序切片版 + 时序边）。

节点 = 设备(内网IP) × 时间窗口；标签 = 四级风险。

两类边：
  1) 通信边：同窗口内设备间通信（真实拓扑）
  2) 时序边：同一设备相邻时间窗口相连（修复切片造成的稀疏）

四级风险分类:
    0 = 正常 Benign (绿)  1 = 侦察 Recon (黄)
    2 = 拒绝服务 DoS/DDoS (橙)  3 = 僵尸网络 Botnet (红)

用法:
    python training/build_device_graph.py [窗口秒数，默认3600]
"""
import sys
import numpy as np
import pandas as pd

DATA = 'd:/Project/iot-ids/training/data/ciciot2023/flows_sample.csv'
OUT_DIR = 'd:/Project/iot-ids/training/data/ciciot2023'

LABEL_TO_LEVEL = {
    'Benign_Final': 0,
    'Recon-PortScan': 1,
    'DoS-TCP_Flood': 2,
    'DDoS-SYN_Flood': 2,
    'Mirai-greip_flood': 3,
    'Mirai-greeth_flood': 3,
    'Mirai-udpplain': 3,
}
LEVEL_NAMES = {0: '正常(绿)', 1: '侦察(黄)', 2: '拒绝服务(橙)', 3: '僵尸网络(红)'}


def is_community(ip) -> bool:
    return str(ip).startswith('192.168.137.')


def main():
    window_size = int(sys.argv[1]) if len(sys.argv) > 1 else 3600
    print(f'窗口大小: {window_size}s ({window_size/3600:.1f} 小时)')

    print('加载数据...')
    df = pd.read_csv(DATA)
    df['level'] = df['label_class'].map(LABEL_TO_LEVEL)

    t0 = df['ts_start'].min()
    df['window'] = ((df['ts_start'] - t0) / window_size).astype(int)

    src = df[df['src_ip'].apply(is_community)].copy()
    src['flow_duration'] = src['ts_end'] - src['ts_start']
    src['is_internal_dst'] = src['dst_ip'].apply(is_community).astype(float)

    print('构建 (设备×窗口) 节点...')
    nodes = src.groupby(['src_ip', 'window']).agg(
        flow_count=('packets', 'count'),
        total_packets=('packets', 'sum'),
        total_bytes=('bytes', 'sum'),
        avg_packets=('packets', 'mean'),
        avg_bytes=('bytes', 'mean'),
        max_packets=('packets', 'max'),
        unique_dst_ports=('dst_port', 'nunique'),
        unique_dst_ips=('dst_ip', 'nunique'),
        unique_src_ports=('src_port', 'nunique'),
        unique_protocols=('protocol', 'nunique'),
        tcp_flags_mean=('tcp_flags', 'mean'),
        avg_flow_duration=('flow_duration', 'mean'),
        internal_ratio=('is_internal_dst', 'mean'),
        level=('level', 'max'),
    ).reset_index()

    node_ids = list(zip(nodes['src_ip'], nodes['window']))
    node_index = {k: i for i, k in enumerate(node_ids)}
    n_nodes = len(nodes)

    feature_names = [c for c in nodes.columns if c not in ('src_ip', 'window', 'level')]
    X = nodes[feature_names].values.astype(np.float32)
    labels = nodes['level'].values.astype(np.int64)

    print(f'  节点总数: {n_nodes:,}')
    print('\n=== 标签分布 ===')
    dist = pd.Series(labels).value_counts().sort_index()
    for lvl, cnt in dist.items():
        print(f'  {lvl} {LEVEL_NAMES[lvl]:<12} {cnt:>7,}')

    adj = np.zeros((n_nodes, n_nodes), dtype=np.float32)

    # ---- 1) 通信边：同窗口设备间通信 ----
    print('\n构建通信边（同窗口设备间通信）...')
    d2d = df[df['src_ip'].apply(is_community) & df['dst_ip'].apply(is_community)].copy()
    d2d['window'] = ((d2d['ts_start'] - t0) / window_size).astype(int)
    comm_edges = 0
    for (sip, dip, win), _ in d2d.groupby(['src_ip', 'dst_ip', 'window']):
        i = node_index.get((sip, win))
        j = node_index.get((dip, win))
        if i is not None and j is not None and i != j:
            if adj[i, j] == 0.0:
                adj[i, j] = 1.0
                adj[j, i] = 1.0
                comm_edges += 1
    print(f'  通信边数: {comm_edges:,}')

    # ---- 2) 时序边：同一设备相邻窗口相连 ----
    print('构建时序边（同设备相邻窗口）...')
    temp_edges = 0
    for ip, grp in nodes.groupby('src_ip'):
        sorted_grp = grp.sort_values('window')
        idxs = [node_index[(ip, w)] for w in sorted_grp['window']]
        for a, b in zip(idxs[:-1], idxs[1:]):
            if adj[a, b] == 0.0:
                adj[a, b] = 1.0
                adj[b, a] = 1.0
                temp_edges += 1
    print(f'  时序边数: {temp_edges:,}')

    total_edges = int(adj.sum() / 2)
    avg_degree = adj.sum() / n_nodes
    print(f'\n=== 图连通性 ===')
    print(f'  总边数: {total_edges:,} (通信 {comm_edges:,} + 时序 {temp_edges:,})')
    print(f'  平均度: {avg_degree:.3f}')

    # 孤立节点检查
    isolated = int((adj.sum(axis=1) == 0).sum())
    print(f'  孤立节点: {isolated} 个 (无任何边)')

    # 标准化特征
    X = np.log1p(X)
    mean = X.mean(axis=0)
    std = X.std(axis=0) + 1e-6
    X = (X - mean) / std

    out = {
        'features': X,
        'adjacency': adj,
        'labels': labels,
        'device_ips': nodes['src_ip'].values.astype(object),
        'windows': nodes['window'].values.astype(np.int64),
        'feature_names': np.array(feature_names, dtype=object),
        'feature_mean': mean.astype(np.float32),
        'feature_std': std.astype(np.float32),
    }
    out_path = f'{OUT_DIR}/device_graph_temporal.npz'
    np.savez(out_path, **out)
    print(f'\n已保存: {out_path}')
    print(f'  features: {X.shape}, adjacency: {adj.shape}, labels: {labels.shape}')
    print(f'  邻接矩阵内存: {adj.nbytes / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
