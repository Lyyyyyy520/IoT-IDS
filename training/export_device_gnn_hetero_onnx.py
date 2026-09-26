"""
导出训练好的 HTGAT（.pt）到 ONNX，并保存特征归一化参数。

相比普通 GAT，多了三个输入：node_type / edge_type / edge_weight。

用法:
    python training/export_device_gnn_hetero_onnx.py
"""
import os
import sys
import numpy as np
import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from backend.models.gat_model_hetero import HeteroGATModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PT_PATH = os.path.join(ROOT, 'training', 'data', 'ciciot2023', 'device_gnn_hetero_best.pt')
GRAPH_PATH = os.path.join(ROOT, 'training', 'data', 'ciciot2023', 'device_graph_temporal.npz')
ONNX_PATH = os.path.join(ROOT, 'backend', 'data', 'device_gnn_hetero.onnx')
NORM_PATH = os.path.join(ROOT, 'backend', 'data', 'device_gnn_norm.npz')

# 与训练脚本保持一致的架构超参
HIDDEN = 64
NUM_HEADS = 4
NUM_LAYERS = 2
DROPOUT = 0.3


def main():
    # 加载 checkpoint
    ckpt = torch.load(PT_PATH, map_location='cpu')
    in_features = int(ckpt['feature_dim'])
    num_classes = int(ckpt['num_classes'])
    print(f'特征维度: {in_features}, 类别数: {num_classes}')

    # 重建模型
    model = HeteroGATModel(
        input_features=in_features,
        num_classes=num_classes,
        hidden=HIDDEN,
        num_heads=NUM_HEADS,
        num_layers=NUM_LAYERS,
        dropout=DROPOUT,
    )
    model.load_state_dict(ckpt['state_dict'])
    model.eval()

    # 导出 ONNX（动态节点数 N，5 个输入）
    N = 8
    dummy_x = torch.randn(N, in_features)
    dummy_adj = torch.ones(N, N)
    dummy_node_type = torch.zeros(N, dtype=torch.long)
    dummy_edge_type = torch.zeros(N, N, dtype=torch.long)
    dummy_edge_weight = torch.ones(N, N)
    torch.onnx.export(
        model,
        (dummy_x, dummy_adj, dummy_node_type, dummy_edge_type, dummy_edge_weight),
        ONNX_PATH,
        input_names=['features', 'adjacency', 'node_type', 'edge_type', 'edge_weight'],
        output_names=['logits'],
        dynamic_axes={
            'features': {0: 'num_nodes'},
            'adjacency': {0: 'num_nodes', 1: 'num_nodes'},
            'node_type': {0: 'num_nodes'},
            'edge_type': {0: 'num_nodes', 1: 'num_nodes'},
            'edge_weight': {0: 'num_nodes', 1: 'num_nodes'},
            'logits': {0: 'num_nodes'},
        },
        opset_version=18,
    )
    print(f'ONNX 导出 -> {ONNX_PATH}')

    # 保存特征归一化参数（log1p + z-score 的 mean/std）
    g = np.load(GRAPH_PATH, allow_pickle=True)
    np.savez(
        NORM_PATH,
        feature_mean=g['feature_mean'].astype(np.float32),
        feature_std=g['feature_std'].astype(np.float32),
        feature_names=g['feature_names'],
    )
    print(f'归一化参数 -> {NORM_PATH}')

    # 验证 ONNX 与 PyTorch 一致性
    print('\n验证 ONNX 与 PyTorch 一致性...')
    import onnxruntime as ort
    sess = ort.InferenceSession(ONNX_PATH, providers=['CPUExecutionProvider'])

    for n in [8, 16, 7]:
        x = np.random.randn(n, in_features).astype(np.float32)
        adj = np.random.randint(0, 2, (n, n)).astype(np.float32)
        adj = np.maximum(adj, adj.T)
        np.fill_diagonal(adj, 0.0)
        node_type = np.random.randint(0, 2, (n,)).astype(np.int64)
        edge_type = np.random.randint(-1, 7, (n, n)).astype(np.int64) * (adj > 0.5)
        edge_weight = np.random.rand(n, n).astype(np.float32) * adj

        with torch.no_grad():
            torch_out = model(
                torch.from_numpy(x), torch.from_numpy(adj),
                torch.from_numpy(node_type), torch.from_numpy(edge_type),
                torch.from_numpy(edge_weight),
            ).numpy()
        onnx_out = sess.run(None, {
            'features': x, 'adjacency': adj, 'node_type': node_type,
            'edge_type': edge_type, 'edge_weight': edge_weight,
        })[0]

        diff = np.abs(torch_out - onnx_out).max()
        print(f'  N={n:2d}: 最大误差 {diff:.2e}  {"OK" if diff < 1e-4 else "FAIL"}')

    print('\n完成！')


if __name__ == '__main__':
    main()
