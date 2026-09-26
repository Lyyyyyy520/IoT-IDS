"""
混合模型实验：对比 GNN(HTGAT) + 树模型 的三种融合方式。

三种混合：
  ① 加权平均：w * p_gnn + (1-w) * p_tree
  ② 堆叠(Stacking)：逻辑回归 on [p_gnn, p_tree]
  ③ 特征拼接：逻辑回归 on [gnn_embedding, raw_features]

同时输出单独 GNN / 单独树模型 的 baseline，供对比。

用法:
    python training/hybrid_experiment.py
"""
import sys
import numpy as np
import torch
import torch.nn.functional as F
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, f1_score, accuracy_score

sys.path.insert(0, 'd:/Project/iot-ids/backend/models')
from gat_model_hetero import HeteroGATModel

GRAPH = 'd:/Project/iot-ids/training/data/ciciot2023/device_graph_temporal.npz'
CKPT = 'd:/Project/iot-ids/training/data/ciciot2023/device_gnn_hetero_best.pt'

LEVEL_NAMES = {0: '正常(绿)', 1: '侦察(黄)', 2: '拒绝服务(橙)', 3: '僵尸网络(红)'}


def report(name, y_true, y_pred):
    acc = accuracy_score(y_true, y_pred)
    macro_f1 = f1_score(y_true, y_pred, average='macro', zero_division=0)
    print(f'\n=== {name} ===')
    print(f'  准确率: {acc:.4f}  宏F1: {macro_f1:.4f}')
    print(classification_report(
        y_true, y_pred, labels=[0, 1, 2, 3],
        target_names=[LEVEL_NAMES[i] for i in range(4)], zero_division=0,
    ))
    return acc, macro_f1


def main():
    # ---- 1. 加载图 ----
    data = np.load(GRAPH, allow_pickle=True)
    X = torch.from_numpy(data['features'].astype(np.float32))
    adj = torch.from_numpy(data['adjacency'].astype(np.float32))
    labels = data['labels'].astype(np.int64)
    node_type = torch.from_numpy(data['node_type'].astype(np.int64))
    edge_type = torch.from_numpy(data['edge_type'].astype(np.int64))
    edge_weight = torch.from_numpy(data['edge_weight'].astype(np.float32))
    device_ips = data['device_ips']
    X_np = X.numpy()

    # ---- 2. 按设备划分（与训练完全一致：np.random.seed(42) + np.random.shuffle）----
    unique_devs = np.unique(device_ips)
    np.random.seed(42)
    np.random.shuffle(unique_devs)
    n = len(unique_devs)
    n_train = int(n * 0.7)
    n_val = int(n * 0.15)
    train_devs = set(unique_devs[:n_train])
    val_devs = set(unique_devs[n_train:n_train + n_val])
    test_devs = set(unique_devs[n_train + n_val:])
    train_mask = np.array([ip in train_devs for ip in device_ips])
    val_mask = np.array([ip in val_devs for ip in device_ips])
    test_mask = np.array([ip in test_devs for ip in device_ips])
    print(f'划分: train {train_mask.sum()} / val {val_mask.sum()} / test {test_mask.sum()}')

    # ---- 3. HTGAT 得到 probs + embedding ----
    ckpt = torch.load(CKPT, map_location='cpu')
    model = HeteroGATModel(input_features=13, num_classes=4, hidden=64, num_heads=4, num_layers=2, dropout=0.3)
    model.load_state_dict(ckpt['state_dict'])
    model.eval()
    with torch.no_grad():
        h = model.encoder(X)
        h = h + model.node_type_embed(node_type)
        for layer, proj in zip(model.gat_layers, model.res_proj):
            h = layer(h, adj, edge_type, edge_weight) + proj(h)
            h = F.relu(h)
        embedding = h.numpy()                       # (N, 256) classifier 之前的表示
        logits = model.classifier(h)
        p_gnn = F.softmax(logits, dim=1).numpy()    # (N, 4)
    print(f'GNN embedding 维度: {embedding.shape[1]}, probs 维度: {p_gnn.shape[1]}')

    # ---- 4. 树模型（GradientBoosting + RandomForest，选更好的）----
    gb = GradientBoostingClassifier(n_estimators=300, learning_rate=0.05, max_depth=6, random_state=42)
    gb.fit(X_np[train_mask], labels[train_mask])
    p_gb = gb.predict_proba(X_np)

    rf = RandomForestClassifier(n_estimators=300, n_jobs=-1, random_state=42)
    rf.fit(X_np[train_mask], labels[train_mask])
    p_rf = rf.predict_proba(X_np)

    # 在 val 上比较 gb 和 rf，选更好的
    gb_val_f1 = f1_score(labels[val_mask], p_gb[val_mask].argmax(1), average='macro', zero_division=0)
    rf_val_f1 = f1_score(labels[val_mask], p_rf[val_mask].argmax(1), average='macro', zero_division=0)
    print(f'\n树模型 val 宏F1: GradientBoosting={gb_val_f1:.4f}, RandomForest={rf_val_f1:.4f}')
    if gb_val_f1 >= rf_val_f1:
        p_tree = p_gb
        tree_name = 'GradientBoosting'
    else:
        p_tree = p_rf
        tree_name = 'RandomForest'
    print(f'选用树模型: {tree_name}')

    # ---- 5. baseline ----
    y_test = labels[test_mask]
    report('单独 GNN(HTGAT)', y_test, p_gnn[test_mask].argmax(1))
    report(f'单独树模型({tree_name})', y_test, p_tree[test_mask].argmax(1))

    # ---- 6. 混合① 加权平均（val 上调 w）----
    best_w, best_val_f1 = 0.5, 0.0
    for w in np.arange(0.0, 1.01, 0.05):
        p = w * p_gnn + (1 - w) * p_tree
        f1 = f1_score(labels[val_mask], p[val_mask].argmax(1), average='macro', zero_division=0)
        if f1 > best_val_f1:
            best_w, best_val_f1 = w, f1
    p_avg = best_w * p_gnn + (1 - best_w) * p_tree
    report(f'混合① 加权平均 (w={best_w:.2f})', y_test, p_avg[test_mask].argmax(1))

    # ---- 7. 混合② 堆叠（逻辑回归 on [p_gnn, p_tree]）----
    stack_train = np.hstack([p_gnn[train_mask], p_tree[train_mask]])
    stack_all = np.hstack([p_gnn, p_tree])
    meta = LogisticRegression(max_iter=1000, random_state=42)
    meta.fit(stack_train, labels[train_mask])
    p_stack = meta.predict_proba(stack_all)
    report('混合② 堆叠', y_test, p_stack[test_mask].argmax(1))

    # ---- 8. 混合③ 特征拼接（逻辑回归 on [embedding, X]）----
    concat_train = np.hstack([embedding[train_mask], X_np[train_mask]])
    concat_all = np.hstack([embedding, X_np])
    meta2 = LogisticRegression(max_iter=2000, random_state=42)
    meta2.fit(concat_train, labels[train_mask])
    p_concat = meta2.predict_proba(concat_all)
    report('混合③ 特征拼接', y_test, p_concat[test_mask].argmax(1))


if __name__ == '__main__':
    main()
