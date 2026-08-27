"""
导出训练好的设备图 GNN（.pt）到 ONNX，并保存特征归一化参数。

复用 backend/models/gat_model.py 的 GATModel（纯 PyTorch，ONNX 友好）。
动态节点数 N，支持现场 6-7 台设备的小图推理。

用法:
    python training/export_device_gnn_onnx.py
"""
import os
import sys
import numpy as np
import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from backend.models.gat_model import GATModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PT_PATH = os.path.join(ROOT, 'training', 'data', 'ciciot2023', 'device_gnn_best.pt')
GRAPH_PATH = os.path.join(ROOT, 'training', 'data', 'ciciot2023', 'device_graph_temporal.npz')
ONNX_PATH = os.path.join(ROOT, 'backend', 'data', 'device_gnn.onnx')
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
    model = GATModel(
        input_features=in_features,
        num_classes=num_classes,
        hidden=HIDDEN,
        num_heads=NUM_HEADS,
        num_layers=NUM_LAYERS,
        dropout=DROPOUT,
    )
    model.load_state_dict(ckpt['state_dict'])
    model.eval()

    # 导出 ONNX（动态节点数 N）
    dummy_x = torch.randn(8, in_features)
    dummy_adj = torch.ones(8, 8)
    torch.onnx.export(
        model,
        (dummy_x, dummy_adj),
        ONNX_PATH,
        input_names=['features', 'adjacency'],
        output_names=['logits'],
        dynamic_axes={
            'features': {0: 'num_nodes'},
            'adjacency': {0: 'num_nodes', 1: 'num_nodes'},
            'logits': {0: 'num_nodes'},
        },
        opset_version=14,
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

    for N in [8, 16, 7]:  # 含现场 7 节点小图
        x = np.random.randn(N, in_features).astype(np.float32)
        adj = np.random.randint(0, 2, (N, N)).astype(np.float32)
        adj = np.maximum(adj, adj.T)
        np.fill_diagonal(adj, 0.0)

        with torch.no_grad():
            torch_out = model(torch.from_numpy(x), torch.from_numpy(adj)).numpy()
        onnx_out = sess.run(None, {'features': x, 'adjacency': adj})[0]

        diff = np.abs(torch_out - onnx_out).max()
        print(f'  N={N:2d}: 最大误差 {diff:.2e}  {"✓" if diff < 1e-4 else "✗"}')

    print('\n完成！')


if __name__ == '__main__':
    main()
