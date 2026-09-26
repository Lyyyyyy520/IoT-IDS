"""
稀疏异构图注意力网络（Sparse HTGAT）训练脚本 —— 本地 CPU 可跑。

与稠密版（gat_model_hetero.py）唯一区别：注意力从 O(N²) 稠密矩阵
改成 O(E) 消息传递（只对真实边算），显存/计算量降 99.9%，本地 CPU 几分钟跑完。

模型结构与权重完全一致，训完可 1:1 转回稠密模型导出 ONNX。

用法:
    python training/train_htgat_sparse.py            # 5000/类
    python training/train_htgat_sparse.py 8000       # 8000/类
    python training/train_htgat_sparse.py --smoke    # 冒烟测试（验证梯度）
"""
import sys
import time
import random
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.metrics import classification_report, f1_score

SEED = 42
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)

_NEG_INF = -1e9
NUM_NODE_TYPES = 2
NUM_EDGE_TYPES = 7

GATEWAY_IP = '192.168.137.1'
EDGE_TEMPORAL = 6
LABEL_TO_LEVEL = {
    'Benign_Final': 0, 'Recon-PortScan': 1, 'DoS-TCP_Flood': 2,
    'DDoS-SYN_Flood': 2, 'Mirai-greip_flood': 3, 'Mirai-greeth_flood': 3, 'Mirai-udpplain': 3,
}
LEVEL_NAMES = {0: '正常(绿)', 1: '侦察(黄)', 2: '拒绝服务(橙)', 3: '僵尸网络(红)'}

DATA_PATH = 'training/data/ciciot2023/flows_sample.csv'


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


# ---------------- 稀疏注意力工具 ----------------

def segment_softmax(e, index, num_nodes):
    """按 src 节点分段的 softmax（正确梯度）。e:(E,) index:(E,)"""
    exp_e = torch.exp(e)  # e ~ O(10)，不会溢出
    sum_exp = torch.scatter_add(
        torch.zeros(num_nodes, dtype=e.dtype, device=e.device), 0, index, exp_e)
    return exp_e / (sum_exp[index] + 1e-9)


# ---------------- 稀疏异构 GAT 模型 ----------------

class SparseHeteroGATLayer(nn.Module):
    """单头稀疏异构 GAT：只对真实边算注意力（消息传递）。"""

    def __init__(self, in_dim, out_dim, dropout=0.1):
        super().__init__()
        self.W = nn.Linear(in_dim, out_dim, bias=False)
        self.a1 = nn.Parameter(torch.empty(out_dim))
        self.a2 = nn.Parameter(torch.empty(out_dim))
        self.edge_bias = nn.Parameter(torch.zeros(NUM_EDGE_TYPES))
        self.leaky = nn.LeakyReLU(0.2)
        self.dropout = nn.Dropout(dropout)
        nn.init.xavier_uniform_(self.W.weight)
        nn.init.xavier_uniform_(self.a1.view(1, -1))
        nn.init.xavier_uniform_(self.a2.view(1, -1))

    def forward(self, h, edge_index, edge_type, edge_weight, num_nodes):
        src, dst = edge_index                      # (E,), (E,)
        z = self.W(h)                              # (N, out)
        # 注意力 logit：e_ij = a1·z_i + a2·z_j（与稠密版公式一致）
        e = (z[src] * self.a1).sum(-1) + (z[dst] * self.a2).sum(-1)
        e = self.leaky(e)
        e = e + self.edge_bias[edge_type]          # 边类型 bias（int64 索引）
        e = e + torch.log1p(edge_weight)           # 边权重
        alpha = segment_softmax(e, src, num_nodes)  # (E,) 按 src 分段 softmax
        alpha = self.dropout(alpha)
        out = torch.index_add(
            torch.zeros(num_nodes, z.shape[1], dtype=h.dtype, device=h.device),
            0, src, alpha.unsqueeze(-1) * z[dst])
        return out


class MultiHeadSparseHeteroGATLayer(nn.Module):
    def __init__(self, in_dim, out_dim, num_heads, dropout=0.1):
        super().__init__()
        self.heads = nn.ModuleList(
            [SparseHeteroGATLayer(in_dim, out_dim, dropout) for _ in range(num_heads)])

    def forward(self, h, edge_index, edge_type, edge_weight, num_nodes):
        return torch.cat(
            [head(h, edge_index, edge_type, edge_weight, num_nodes) for head in self.heads], dim=-1)


class SparseHeteroGATModel(nn.Module):
    def __init__(self, input_features=13, num_classes=4, hidden=64, num_heads=4, num_layers=2, dropout=0.3):
        super().__init__()
        self.node_type_embed = nn.Embedding(NUM_NODE_TYPES, hidden)
        self.encoder = nn.Sequential(
            nn.Linear(input_features, hidden), nn.ReLU(inplace=True),
            nn.Linear(hidden, hidden), nn.ReLU(inplace=True))
        self.gat_layers = nn.ModuleList()
        self.res_proj = nn.ModuleList()
        in_dim = hidden
        for _ in range(num_layers):
            self.gat_layers.append(MultiHeadSparseHeteroGATLayer(in_dim, hidden, num_heads, dropout))
            out_dim = hidden * num_heads
            self.res_proj.append(nn.Linear(in_dim, out_dim) if in_dim != out_dim else nn.Identity())
            in_dim = out_dim
        self.classifier = nn.Sequential(
            nn.Linear(in_dim, 64), nn.ReLU(inplace=True),
            nn.Dropout(dropout), nn.Linear(64, num_classes))

    def forward(self, x, edge_index, node_type, edge_type, edge_weight):
        h = self.encoder(x) + self.node_type_embed(node_type)
        num_nodes = x.shape[0]
        for layer, proj in zip(self.gat_layers, self.res_proj):
            h = layer(h, edge_index, edge_type, edge_weight, num_nodes) + proj(h)
            h = F.relu(h)
        return self.classifier(h)


class FocalLoss(nn.Module):
    """Focal Loss：自动给难样本（如侦察类）加权，改善类别不平衡。"""
    def __init__(self, gamma=2.0, alpha=None):
        super().__init__()
        self.gamma = gamma
        self.alpha = alpha

    def forward(self, logits, targets):
        ce = F.cross_entropy(logits, targets, reduction='none', weight=self.alpha)
        pt = torch.exp(-ce)
        return (((1 - pt) ** self.gamma) * ce).mean()


# ---------------- 稀疏建图 ----------------

def build_graph_sparse(df, window_size=300, max_per_class=8000):
    df = df.copy()
    df['level'] = df['label_class'].map(LABEL_TO_LEVEL)
    t0 = df['ts_start'].min()
    df['window'] = ((df['ts_start'] - t0) / window_size).astype(int)

    src = df[df['src_ip'].apply(is_community)].copy()
    src['flow_duration'] = src['ts_end'] - src['ts_start']
    src['is_internal_dst'] = src['dst_ip'].apply(is_community).astype(float)

    nodes = src.groupby(['src_ip', 'window']).agg(
        flow_count=('packets', 'count'), total_packets=('packets', 'sum'), total_bytes=('bytes', 'sum'),
        avg_packets=('packets', 'mean'), avg_bytes=('bytes', 'mean'), max_packets=('packets', 'max'),
        unique_dst_ports=('dst_port', 'nunique'), unique_dst_ips=('dst_ip', 'nunique'),
        unique_src_ports=('src_port', 'nunique'), unique_protocols=('protocol', 'nunique'),
        tcp_flags_mean=('tcp_flags', 'mean'), avg_flow_duration=('flow_duration', 'mean'),
        internal_ratio=('is_internal_dst', 'mean'), level=('level', 'max')).reset_index()

    sampled = []
    for lvl in sorted(nodes['level'].unique()):
        sub = nodes[nodes['level'] == lvl]
        if len(sub) > max_per_class:
            sub = sub.sample(max_per_class, random_state=SEED)
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

    # ---- 建边（列表，而非 N×N 矩阵）----
    src_list, dst_list, et_list, ew_list = [], [], [], []

    d2d = df[df['src_ip'].apply(is_community) & df['dst_ip'].apply(is_community)].copy()
    d2d['window'] = ((d2d['ts_start'] - t0) / window_size).astype(int)
    for (sip, dip, win), grp in d2d.groupby(['src_ip', 'dst_ip', 'window']):
        i = node_index.get((sip, win))
        j = node_index.get((dip, win))
        if i is not None and j is not None and i != j:
            et = port_to_edge_type(grp['dst_port'].mode()[0])
            w = float(len(grp))
            src_list += [i, j]; dst_list += [j, i]
            et_list += [et, et]; ew_list += [w, w]

    for ip, grp in nodes.groupby('src_ip'):
        sorted_grp = grp.sort_values('window')
        idxs = [node_index[(ip, w)] for w in sorted_grp['window']]
        for a, b in zip(idxs[:-1], idxs[1:]):
            src_list += [a, b]; dst_list += [b, a]
            et_list += [EDGE_TEMPORAL, EDGE_TEMPORAL]
            ew_list += [1.0, 1.0]

    edge_index = torch.tensor([src_list, dst_list], dtype=torch.long)   # (2, E)
    edge_type = torch.tensor(et_list, dtype=torch.long)                 # (E,)
    edge_weight = torch.tensor(ew_list, dtype=torch.float32)            # (E,)
    print(f'边数: {edge_index.shape[1]:,}')

    X = np.log1p(X)
    mean = X.mean(axis=0)
    std = X.std(axis=0) + 1e-6
    X = (X - mean) / std
    return X, edge_index, edge_type, edge_weight, labels, node_type, nodes['src_ip'].values, mean, std


# ---------------- 训练 ----------------

def train(X, edge_index, edge_type, edge_weight, labels, node_type, device_ips, out_path, max_epochs=300, gamma=2.0):
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f'训练设备: {device}')

    X = torch.from_numpy(X).to(device)
    edge_index = edge_index.to(device)
    edge_type = edge_type.to(device)
    edge_weight = edge_weight.to(device)
    labels = torch.from_numpy(labels).to(device)
    node_type = torch.from_numpy(node_type).to(device)

    unique_devs = np.unique(device_ips)
    np.random.seed(SEED); np.random.shuffle(unique_devs)
    n = len(unique_devs); n_train = int(n * 0.7); n_val = int(n * 0.15)
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

    model = SparseHeteroGATModel().to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f'模型参数量: {n_params:,} ({n_params/1e6:.3f}M)')

    optimizer = torch.optim.Adam(model.parameters(), lr=0.005, weight_decay=5e-4)
    # gamma=0 时 FocalLoss 退化为类别加权交叉熵
    criterion = FocalLoss(gamma=gamma, alpha=class_weights)
    print(f'损失函数: FocalLoss(gamma={gamma})' if gamma > 0 else '损失函数: 类别加权交叉熵 (gamma=0)')

    best_val_f1, best_state, patience = 0.0, None, 0
    for epoch in range(1, max_epochs + 1):
        t0 = time.time()
        model.train(); optimizer.zero_grad()
        logits = model(X, edge_index, node_type, edge_type, edge_weight)
        loss = criterion(logits[train_mask], labels[train_mask])
        loss.backward(); optimizer.step()

        model.eval()
        with torch.no_grad():
            logits = model(X, edge_index, node_type, edge_type, edge_weight)
            val_pred = logits[val_mask].argmax(1)
            val_labels = labels[val_mask]
            val_f1 = f1_score(val_labels.cpu().numpy(), val_pred.cpu().numpy(), average='macro', zero_division=0)

        if val_f1 > best_val_f1:
            best_val_f1, best_state, patience = val_f1, {k: v.clone() for k, v in model.state_dict().items()}, 0
            torch.save({'state_dict': best_state, 'feature_dim': 13, 'num_classes': 4}, out_path)
        else:
            patience += 1

        if epoch % 5 == 0 or epoch == 1:
            dt = time.time() - t0
            print(f'  epoch {epoch:3d}  loss {loss.item():.4f}  val_f1 {val_f1:.4f}  dt {dt:.1f}s')
        if patience >= 20:
            print(f'  早停 epoch {epoch} (best {best_val_f1:.4f})')
            break

    model.load_state_dict(best_state); model.eval()
    with torch.no_grad():
        logits = model(X, edge_index, node_type, edge_type, edge_weight)
        test_pred = logits[test_mask].argmax(1)
        test_labels = labels[test_mask]
    print('\n分类报告:')
    print(classification_report(test_labels.cpu().numpy(), test_pred.cpu().numpy(),
                                labels=[0, 1, 2, 3], target_names=[LEVEL_NAMES[i] for i in range(4)], zero_division=0))
    torch.save({'state_dict': best_state, 'feature_dim': 13, 'num_classes': 4}, out_path)
    print(f'\n已保存: {out_path}')


# ---------------- 冒烟测试（验证稀疏实现梯度正确） ----------------

def smoke_test():
    print('冒烟测试：验证稀疏 GAT 梯度与输出形状...')
    model = SparseHeteroGATModel()
    N, E = 8, 12
    x = torch.randn(N, 13)
    edge_index = torch.randint(0, N, (2, E))
    node_type = torch.randint(0, 2, (N,))
    edge_type = torch.randint(0, 7, (E,))
    edge_weight = torch.rand(E)
    logits = model(x, edge_index, node_type, edge_type, edge_weight)
    loss = F.cross_entropy(logits, torch.randint(0, 4, (N,)))
    loss.backward()
    grads = [p.grad for p in model.parameters() if p.grad is not None]
    ok = all(g is not None and torch.isfinite(g).all() for g in grads)
    print(f'  输出形状: {logits.shape}  参数梯度数: {len(grads)}  梯度有限: {ok}')
    return ok


if __name__ == '__main__':
    if '--smoke' in sys.argv:
        sys.exit(0 if smoke_test() else 1)

    MAX_PER_CLASS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 5000
    GAMMA = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    OUT = 'training/data/ciciot2023/device_gnn_hetero_sparse.pt'

    print(f'加载 {DATA_PATH} ...')
    df = pd.read_csv(DATA_PATH)
    X, edge_index, edge_type, edge_weight, labels, node_type, device_ips, mean, std = \
        build_graph_sparse(df, 300, MAX_PER_CLASS)
    train(X, edge_index, edge_type, edge_weight, labels, node_type, device_ips, OUT, gamma=GAMMA)
