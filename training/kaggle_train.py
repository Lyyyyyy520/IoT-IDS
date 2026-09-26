"""
Kaggle GPU 训练脚本（自包含：建图 + GPU 训练 + 导出 ONNX）。

用法（在 Kaggle Notebook 里，开启 GPU 加速）:
    # 1. 把本脚本 + gat_model_hetero.py + flows_sample.csv 上传为 Dataset
    # 2. Notebook 里 Add Data 添加该 Dataset
    # 3. 运行下面的代码

    import sys
    sys.path.insert(0, '/kaggle/input/<你的dataset名>/')
    DATA = '/kaggle/input/<你的dataset名>/flows_sample.csv'
    # 然后运行本脚本的 main(DATA, ...)
"""
import sys
import os
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.metrics import classification_report, confusion_matrix, f1_score

# 端口 → 边类型映射（与训练一致）
GATEWAY_IP = '192.168.137.1'
EDGE_TEMPORAL = 6
LABEL_TO_LEVEL = {
    'Benign_Final': 0, 'Recon-PortScan': 1, 'DoS-TCP_Flood': 2,
    'DDoS-SYN_Flood': 2, 'Mirai-greip_flood': 3, 'Mirai-greeth_flood': 3, 'Mirai-udpplain': 3,
}
LEVEL_NAMES = {0: '正常(绿)', 1: '侦察(黄)', 2: '拒绝服务(橙)', 3: '僵尸网络(红)'}


def is_community(ip):
    return str(ip).startswith('192.168.137.')


def port_to_edge_type(port):
    port = int(port)
    if port in (1883, 8883): return 0
    if port in (80, 443): return 1
    if port == 53: return 2
    if port == 123: return 3
    if port in (5683, 5684): return 4
    return 5


def build_graph(df, window_size=300, max_per_class=8000):
    """建设备图（异构），返回 features/adj/labels/node_type/edge_type/edge_weight + 归一化参数。"""
    df = df.copy()
    df['level'] = df['label_class'].map(LABEL_TO_LEVEL)
    t0 = df['ts_start'].min()
    df['window'] = ((df['ts_start'] - t0) / window_size).astype(int)

    src = df[df['src_ip'].apply(is_community)].copy()
    src['flow_duration'] = src['ts_end'] - src['ts_start']
    src['is_internal_dst'] = src['dst_ip'].apply(is_community).astype(float)

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

    # 下采样
    sampled = []
    for lvl in sorted(nodes['level'].unique()):
        sub = nodes[nodes['level'] == lvl]
        if len(sub) > max_per_class:
            sub = sub.sample(max_per_class, random_state=42)
        sampled.append(sub)
    nodes = pd.concat(sampled).reset_index(drop=True)
    print(f'下采样后节点数: {len(nodes):,}')

    node_ids = list(zip(nodes['src_ip'], nodes['window']))
    node_index = {k: i for i, k in enumerate(node_ids)}
    n_nodes = len(nodes)

    FEATURES = ['flow_count', 'total_packets', 'total_bytes', 'avg_packets', 'avg_bytes',
                'max_packets', 'unique_dst_ports', 'unique_dst_ips', 'unique_src_ports',
                'unique_protocols', 'tcp_flags_mean', 'avg_flow_duration', 'internal_ratio']
    X = nodes[FEATURES].values.astype(np.float32)
    labels = nodes['level'].values.astype(np.int64)
    node_type = np.where(nodes['src_ip'].values == GATEWAY_IP, 0, 1).astype(np.int64)

    adj = np.zeros((n_nodes, n_nodes), dtype=np.float32)
    edge_type = np.full((n_nodes, n_nodes), -1, dtype=np.int8)
    edge_weight = np.zeros((n_nodes, n_nodes), dtype=np.float32)

    d2d = df[df['src_ip'].apply(is_community) & df['dst_ip'].apply(is_community)].copy()
    d2d['window'] = ((d2d['ts_start'] - t0) / window_size).astype(int)
    for (sip, dip, win), grp in d2d.groupby(['src_ip', 'dst_ip', 'window']):
        i = node_index.get((sip, win))
        j = node_index.get((dip, win))
        if i is not None and j is not None and i != j and adj[i, j] == 0.0:
            adj[i, j] = adj[j, i] = 1.0
            et = port_to_edge_type(grp['dst_port'].mode()[0])
            edge_type[i, j] = edge_type[j, i] = et
            edge_weight[i, j] = edge_weight[j, i] = len(grp)

    for ip, grp in nodes.groupby('src_ip'):
        sorted_grp = grp.sort_values('window')
        idxs = [node_index[(ip, w)] for w in sorted_grp['window']]
        for a, b in zip(idxs[:-1], idxs[1:]):
            if adj[a, b] == 0.0:
                adj[a, b] = adj[b, a] = 1.0
                edge_type[a, b] = edge_type[b, a] = EDGE_TEMPORAL
                edge_weight[a, b] = edge_weight[b, a] = 1.0

    X = np.log1p(X)
    mean = X.mean(axis=0)
    std = X.std(axis=0) + 1e-6
    X = (X - mean) / std
    return X, adj, labels, node_type, edge_type, edge_weight, nodes['src_ip'].values, mean, std


def train(X, adj, labels, node_type, edge_type, edge_weight, device_ips, out_path):
    """训练 HTGAT（GPU，fp32）。5000/类 ≈ 1.5 万节点，fp32 在 14GB 显存内即可跑通（无需 fp16）。"""
    from gat_model_hetero import HeteroGATModel
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f'训练设备: {device}')
    torch.cuda.empty_cache()

    X = torch.from_numpy(X).to(device)
    adj = torch.from_numpy(adj).to(device)
    labels = torch.from_numpy(labels).to(device)
    node_type = torch.from_numpy(node_type).to(device)
    edge_type = torch.from_numpy(edge_type).to(device)  # 保持 int8，模型内部会 .int() 转索引
    edge_weight = torch.from_numpy(edge_weight).to(device)

    unique_devs = np.unique(device_ips)
    np.random.seed(42)
    np.random.shuffle(unique_devs)
    n = len(unique_devs)
    n_train = int(n * 0.7)
    n_val = int(n * 0.15)
    train_devs = set(unique_devs[:n_train])
    val_devs = set(unique_devs[n_train:n_train + n_val])
    test_devs = set(unique_devs[n_train + n_val:])
    train_mask = torch.from_numpy(np.array([ip in train_devs for ip in device_ips])).to(device)
    val_mask = torch.from_numpy(np.array([ip in val_devs for ip in device_ips])).to(device)
    test_mask = torch.from_numpy(np.array([ip in test_devs for ip in device_ips])).to(device)

    train_labels = labels[train_mask].cpu().numpy()
    counts = np.bincount(train_labels, minlength=4).astype(np.float32)
    counts = np.where(counts == 0, 1.0, counts)
    class_weights = torch.from_numpy(len(train_labels) / (4 * counts)).to(device)

    model = HeteroGATModel(input_features=13, num_classes=4, hidden=64, num_heads=4, num_layers=2, dropout=0.3).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.005, weight_decay=5e-4)
    criterion = nn.CrossEntropyLoss(weight=class_weights)

    best_val_f1, best_state, patience = 0.0, None, 0
    for epoch in range(1, 301):
        model.train()
        optimizer.zero_grad()
        logits = model(X, adj, node_type, edge_type, edge_weight)
        loss = criterion(logits[train_mask], labels[train_mask])
        loss.backward()
        optimizer.step()

        model.eval()
        with torch.no_grad():
            logits = model(X, adj, node_type, edge_type, edge_weight)
            val_pred = logits[val_mask].argmax(1)
            val_labels = labels[val_mask]
            val_f1 = f1_score(val_labels.cpu().numpy(), val_pred.cpu().numpy(), average='macro', zero_division=0)

        if val_f1 > best_val_f1:
            best_val_f1, best_state, patience = val_f1, {k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            patience += 1
        if epoch % 20 == 0:
            print(f'  epoch {epoch:3d}  loss {loss.item():.4f}  val_macroF1 {val_f1:.4f}')
        if patience >= 40:
            print(f'  早停 epoch {epoch} (best {best_val_f1:.4f})')
            break

    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        logits = model(X, adj, node_type, edge_type, edge_weight)
        test_pred = logits[test_mask].argmax(1)
        test_labels = labels[test_mask]
    print('\n分类报告:')
    print(classification_report(test_labels.cpu().numpy(), test_pred.cpu().numpy(),
                                labels=[0, 1, 2, 3], target_names=[LEVEL_NAMES[i] for i in range(4)], zero_division=0))

    torch.save({'state_dict': best_state, 'feature_dim': 13, 'num_classes': 4}, out_path)
    print(f'\n已保存: {out_path}')


if __name__ == '__main__':
    DATA = sys.argv[1] if len(sys.argv) > 1 else 'flows_sample.csv'
    OUT = sys.argv[2] if len(sys.argv) > 2 else 'device_gnn_hetero_8k_best.pt'
    MAX_PER_CLASS = int(sys.argv[3]) if len(sys.argv) > 3 else 8000
    print(f'加载 {DATA} ...')
    df = pd.read_csv(DATA)
    X, adj, labels, node_type, edge_type, edge_weight, device_ips, mean, std = build_graph(df, 300, MAX_PER_CLASS)
    train(X, adj, labels, node_type, edge_type, edge_weight, device_ips, OUT)
