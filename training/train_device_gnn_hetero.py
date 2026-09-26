"""
训练异构图注意力网络（HTGAT）—— 设备级四级风险分类。

相比普通 GAT，额外使用节点类型 / 边类型 / 边权重（见 gat_model_hetero.py）。

关键设计（同 train_device_gnn.py）:
  - 按设备划分 train/val/test（避免时间泄漏）
  - 类别权重处理不平衡
  - 转导学习：用全图邻接做消息传递，只在训练节点算 loss

用法:
    python training/train_device_gnn_hetero.py
"""
import sys
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.metrics import classification_report, confusion_matrix, f1_score

sys.path.insert(0, 'd:/Project/iot-ids/backend/models')
from gat_model_hetero import HeteroGATModel

GRAPH = sys.argv[1] if len(sys.argv) > 1 else 'd:/Project/iot-ids/training/data/ciciot2023/device_graph_temporal.npz'
OUT = sys.argv[2] if len(sys.argv) > 2 else 'd:/Project/iot-ids/training/data/ciciot2023/device_gnn_hetero_best.pt'

LEVEL_NAMES = {0: '正常(绿)', 1: '侦察(黄)', 2: '拒绝服务(橙)', 3: '僵尸网络(红)'}
NUM_CLASSES = 4
SEED = 42


def set_seed(seed):
    np.random.seed(seed)
    torch.manual_seed(seed)


def main():
    set_seed(SEED)

    # ---- 加载异构图 ----
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f'训练设备: {device}')
    data = np.load(GRAPH, allow_pickle=True)
    X = torch.from_numpy(data['features'].astype(np.float32)).to(device)
    adj = torch.from_numpy(data['adjacency'].astype(np.float32)).to(device)
    labels = torch.from_numpy(data['labels'].astype(np.int64)).to(device)
    node_type = torch.from_numpy(data['node_type'].astype(np.int64)).to(device)
    edge_type = torch.from_numpy(data['edge_type'].astype(np.int64)).to(device)
    edge_weight = torch.from_numpy(data['edge_weight'].astype(np.float32)).to(device)
    device_ips = data['device_ips']
    print(f'图: {X.shape[0]} 节点, {X.shape[1]} 特征, {int(adj.sum()/2)} 边')
    print(f'标签分布: {np.bincount(labels.cpu().numpy())}')
    print(f'节点类型: {np.bincount(node_type.cpu().numpy())} (gateway/device)')
    et = edge_type.cpu().numpy()
    print(f'边类型分布: {np.bincount(et[et >= 0], minlength=7)}')

    # ---- 按设备划分 train/val/test (70/15/15) ----
    unique_devs = np.unique(device_ips)
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
    print(f'设备划分: train {len(train_devs)} / val {len(val_devs)} / test {len(test_devs)}')

    # ---- 类别权重 ----
    train_labels = labels[train_mask].cpu().numpy()
    counts = np.bincount(train_labels, minlength=NUM_CLASSES).astype(np.float32)
    counts = np.where(counts == 0, 1.0, counts)
    weights = len(train_labels) / (NUM_CLASSES * counts)
    class_weights = torch.from_numpy(weights.astype(np.float32)).to(device)
    print(f'类别权重: {class_weights.cpu().numpy().round(3)}')

    # ---- 模型 ----
    model = HeteroGATModel(
        input_features=X.shape[1],
        num_classes=NUM_CLASSES,
        hidden=64,
        num_heads=4,
        num_layers=2,
        dropout=0.3,
    ).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f'模型参数量: {n_params:,} ({n_params/1e6:.3f}M)')

    optimizer = torch.optim.Adam(model.parameters(), lr=0.005, weight_decay=5e-4)
    criterion = nn.CrossEntropyLoss(weight=class_weights)

    # ---- 训练循环（早停） ----
    best_val_f1 = 0.0
    best_state = None
    patience = 0
    max_patience = 40

    print('\n开始训练...')
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
            val_acc = (val_pred == val_labels).float().mean().item()
            val_f1 = f1_score(val_labels.cpu().numpy(), val_pred.cpu().numpy(), average='macro', zero_division=0)

        if val_f1 > best_val_f1:
            best_val_f1 = val_f1
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience = 0
        else:
            patience += 1

        if epoch % 20 == 0 or epoch == 1:
            print(f'  epoch {epoch:3d}  loss {loss.item():.4f}  val_acc {val_acc:.4f}  val_macroF1 {val_f1:.4f}')

        if patience >= max_patience:
            print(f'  早停于 epoch {epoch} (best val_macroF1={best_val_f1:.4f})')
            break

    # ---- 测试评估 ----
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        logits = model(X, adj, node_type, edge_type, edge_weight)
        test_pred = logits[test_mask].argmax(1)
        test_labels = labels[test_mask]

    test_acc = (test_pred == test_labels).float().mean().item()
    print(f'\n=== 测试集评估 (best val_macroF1={best_val_f1:.4f}) ===')
    print(f'测试准确率: {test_acc:.4f}')
    print('\n分类报告:')
    print(classification_report(
        test_labels.cpu().numpy(), test_pred.cpu().numpy(),
        labels=list(range(4)),
        target_names=[LEVEL_NAMES[i] for i in range(4)],
        zero_division=0,
    ))
    print('混淆矩阵 (行=真实, 列=预测):')
    print(confusion_matrix(test_labels.cpu().numpy(), test_pred.cpu().numpy()))

    torch.save({'state_dict': best_state, 'feature_dim': X.shape[1], 'num_classes': NUM_CLASSES}, OUT)
    print(f'\n已保存模型: {OUT}')


if __name__ == '__main__':
    main()
