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


# 网关 IP（社区网段第一个 IP）
GATEWAY_IP = '192.168.137.1'

# 边类型：0=mqtt 1=http 2=dns 3=ntp 4=coap 5=other 6=时序边
EDGE_TEMPORAL = 6


def port_to_edge_type(port) -> int:
    """把目的端口映射成边类型（协议）。"""
    port = int(port)
    if port in (1883, 8883):
        return 0  # mqtt
    if port in (80, 443):
        return 1  # http
    if port == 53:
        return 2  # dns
    if port == 123:
        return 3  # ntp
    if port in (5683, 5684):
        return 4  # coap
    return 5  # other


def main():
    window_size = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    max_per_class = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
    use_extra = len(sys.argv) > 3 and sys.argv[3] == '1'
    out_name = sys.argv[4] if len(sys.argv) > 4 else 'device_graph_temporal'
    print(f'窗口: {window_size}s, 每类最多: {max_per_class}, 额外特征: {use_extra}, 输出: {out_name}')

    print('加载数据...')
    df = pd.read_csv(DATA)
    df['level'] = df['label_class'].map(LABEL_TO_LEVEL)

    t0 = df['ts_start'].min()
    df['window'] = ((df['ts_start'] - t0) / window_size).astype(int)

    src = df[df['src_ip'].apply(is_community)].copy()
    src['flow_duration'] = src['ts_end'] - src['ts_start']
    src['is_internal_dst'] = src['dst_ip'].apply(is_community).astype(float)
    # 额外特征：协议占比 / 平均包大小
    src['is_tcp'] = (src['protocol'] == 6).astype(float)
    src['is_udp'] = (src['protocol'] == 17).astype(float)
    src['packet_size'] = (src['bytes'] / src['packets'].clip(lower=1))

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
        tcp_ratio=('is_tcp', 'mean'),
        udp_ratio=('is_udp', 'mean'),
        avg_packet_size=('packet_size', 'mean'),
        dst_port_std=('dst_port', 'std'),
        dst_port_mean=('dst_port', 'mean'),
        src_port_std=('src_port', 'std'),
        packets_std=('packets', 'std'),
        bytes_std=('bytes', 'std'),
        level=('level', 'max'),
    ).reset_index()
    # std 类特征在"单条流"的分组里是 NaN，填 0（否则训练 loss 变 NaN）
    nodes = nodes.fillna(0)

    # 下采样：节点过多会让稠密 GAT 内存爆炸（O(N²)），按类限流
    sampled = []
    for lvl in sorted(nodes['level'].unique()):
        sub = nodes[nodes['level'] == lvl]
        if len(sub) > max_per_class:
            sub = sub.sample(max_per_class, random_state=42)
        sampled.append(sub)
    nodes = pd.concat(sampled).reset_index(drop=True)
    print(f'  下采样后节点数: {len(nodes):,} (每类最多 {max_per_class})')

    node_ids = list(zip(nodes['src_ip'], nodes['window']))
    node_index = {k: i for i, k in enumerate(node_ids)}
    n_nodes = len(nodes)

    BASE_FEATURES = [
        'flow_count', 'total_packets', 'total_bytes', 'avg_packets', 'avg_bytes',
        'max_packets', 'unique_dst_ports', 'unique_dst_ips', 'unique_src_ports',
        'unique_protocols', 'tcp_flags_mean', 'avg_flow_duration', 'internal_ratio',
    ]
    EXTRA_FEATURES = [
        'tcp_ratio', 'udp_ratio', 'avg_packet_size', 'dst_port_std',
        'dst_port_mean', 'src_port_std', 'packets_std', 'bytes_std',
    ]
    feature_names = BASE_FEATURES + EXTRA_FEATURES if use_extra else BASE_FEATURES
    X = nodes[feature_names].values.astype(np.float32)
    labels = nodes['level'].values.astype(np.int64)

    # 节点类型：gateway=0, device=1
    node_type = np.where(nodes['src_ip'].values == GATEWAY_IP, 0, 1).astype(np.int64)

    print(f'  节点总数: {n_nodes:,}')
    print('\n=== 标签分布 ===')
    dist = pd.Series(labels).value_counts().sort_index()
    for lvl, cnt in dist.items():
        print(f'  {lvl} {LEVEL_NAMES[lvl]:<12} {cnt:>7,}')

    adj = np.zeros((n_nodes, n_nodes), dtype=np.float32)
    edge_type = np.full((n_nodes, n_nodes), -1, dtype=np.int8)   # -1 = 无边，边类型 0~6
    edge_weight = np.zeros((n_nodes, n_nodes), dtype=np.float32)

    # ---- 1) 通信边：同窗口设备间通信（带协议类型 + 频率权重）----
    print('\n构建通信边（同窗口设备间通信）...')
    d2d = df[df['src_ip'].apply(is_community) & df['dst_ip'].apply(is_community)].copy()
    d2d['window'] = ((d2d['ts_start'] - t0) / window_size).astype(int)
    comm_edges = 0
    for (sip, dip, win), grp in d2d.groupby(['src_ip', 'dst_ip', 'window']):
        i = node_index.get((sip, win))
        j = node_index.get((dip, win))
        if i is not None and j is not None and i != j:
            if adj[i, j] == 0.0:
                adj[i, j] = 1.0
                adj[j, i] = 1.0
                # 协议类型（取最常见的目的端口）
                et = port_to_edge_type(grp['dst_port'].mode()[0])
                # 频率权重（该设备对的流数量）
                freq = len(grp)
                edge_type[i, j] = et
                edge_type[j, i] = et
                edge_weight[i, j] = freq
                edge_weight[j, i] = freq
                comm_edges += 1
    print(f'  通信边数: {comm_edges:,}')

    # ---- 2) 时序边：同一设备相邻窗口相连（边类型 = 时序，权重 = 1）----
    print('构建时序边（同设备相邻窗口）...')
    temp_edges = 0
    for ip, grp in nodes.groupby('src_ip'):
        sorted_grp = grp.sort_values('window')
        idxs = [node_index[(ip, w)] for w in sorted_grp['window']]
        for a, b in zip(idxs[:-1], idxs[1:]):
            if adj[a, b] == 0.0:
                adj[a, b] = 1.0
                adj[b, a] = 1.0
                edge_type[a, b] = EDGE_TEMPORAL
                edge_type[b, a] = EDGE_TEMPORAL
                edge_weight[a, b] = 1.0
                edge_weight[b, a] = 1.0
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
        'node_type': node_type,
        'edge_type': edge_type,
        'edge_weight': edge_weight,
        'device_ips': nodes['src_ip'].values.astype(object),
        'windows': nodes['window'].values.astype(np.int64),
        'feature_names': np.array(feature_names, dtype=object),
        'feature_mean': mean.astype(np.float32),
        'feature_std': std.astype(np.float32),
    }
    out_path = f'{OUT_DIR}/{out_name}.npz'
    np.savez(out_path, **out)
    print(f'\n已保存: {out_path}')
    print(f'  features: {X.shape}, adjacency: {adj.shape}, labels: {labels.shape}')
    print(f'  邻接矩阵内存: {adj.nbytes / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
