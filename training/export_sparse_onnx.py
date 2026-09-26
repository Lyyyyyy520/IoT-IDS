"""
把稀疏训练好的 HTGAT 权重转回稠密模型，导出 ONNX + 归一化参数。

稀疏模型（train_htgat_sparse.py）与稠密模型（gat_model_hetero.py）
权重结构完全一致，直接 load_state_dict 转移，然后导出 ONNX 部署树莓派。

用法:
    python training/export_sparse_onnx.py
"""
import os
import sys
import numpy as np
import pandas as pd
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'training'))

from backend.models.gat_model_hetero import HeteroGATModel
from train_htgat_sparse import build_graph_sparse

PT_PATH = os.path.join(ROOT, 'training', 'data', 'ciciot2023', 'device_gnn_hetero_sparse.pt')
CSV_PATH = os.path.join(ROOT, 'training', 'data', 'ciciot2023', 'flows_sample.csv')
ONNX_PATH = os.path.join(ROOT, 'backend', 'data', 'device_gnn_hetero.onnx')
NORM_PATH = os.path.join(ROOT, 'backend', 'data', 'device_gnn_norm.npz')

FEATURE_NAMES = ['flow_count', 'total_packets', 'total_bytes', 'avg_packets', 'avg_bytes',
                 'max_packets', 'unique_dst_ports', 'unique_dst_ips', 'unique_src_ports',
                 'unique_protocols', 'tcp_flags_mean', 'avg_flow_duration', 'internal_ratio']


def main():
    # 1. 加载稀疏权重
    ckpt = torch.load(PT_PATH, map_location='cpu')
    in_features = int(ckpt['feature_dim'])
    num_classes = int(ckpt['num_classes'])
    print(f'特征维度: {in_features}, 类别数: {num_classes}')

    # 2. 重跑建图获取归一化参数（确定性 SEED=42，与训练时一致）
    print('重跑建图以获取归一化参数...')
    df = pd.read_csv(CSV_PATH)
    _, _, _, _, _, _, _, mean, std = build_graph_sparse(df, 300, 5000)

    # 3. 重建稠密模型 + 加载稀疏权重
    model = HeteroGATModel(
        input_features=in_features, num_classes=num_classes,
        hidden=64, num_heads=4, num_layers=2, dropout=0.3,
    )
    missing, unexpected = model.load_state_dict(ckpt['state_dict'], strict=False)
    print(f'权重转移: 缺失 {len(missing)} 个键, 多余 {len(unexpected)} 个键')
    if missing:
        print('  缺失示例:', missing[:3])
    if unexpected:
        print('  多余示例:', unexpected[:3])
    assert not missing and not unexpected, '权重键名不匹配，请检查模型结构'
    model.eval()

    # 4. 导出 ONNX（动态节点数 N，5 个输入）
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
        dynamo=False,
    )
    print(f'ONNX 导出 -> {ONNX_PATH}')

    # 5. 保存归一化参数
    np.savez(
        NORM_PATH,
        feature_mean=mean.astype(np.float32),
        feature_std=std.astype(np.float32),
        feature_names=np.array(FEATURE_NAMES),
    )
    print(f'归一化参数 -> {NORM_PATH}')

    # 6. 验证 ONNX 与 PyTorch 一致性
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
