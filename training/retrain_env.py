"""域适配重训编排：环境形态数据 + CICIoT2023 混合 -> 重训 -> 导出 ONNX + norm。

对应 docs/11 阶段5「现场流量微调」的第一阶段（环境形态合成数据）；
排练时抓到的真实流量可继续走同一条管线做最终微调。

用法:
    python training/retrain_env.py [max_per_class=8000] [gamma=2.0] [max_epochs=150]
"""
import os
import sys

import numpy as np
import pandas as pd
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'training'))

from train_htgat_sparse import build_graph_sparse, train  # noqa: E402
from backend.models.gat_model_hetero import HeteroGATModel  # noqa: E402

ENV_CSV = os.path.join(ROOT, 'training', 'data', 'env', 'env_training_flows.csv')
REAL_CSV = os.path.join(ROOT, 'training', 'data', 'real', 'real_training_flows.csv')
CICIOT_CSV = os.path.join(ROOT, 'training', 'data', 'ciciot2023', 'flows_sample.csv')
COMBINED_CSV = os.path.join(ROOT, 'training', 'data', 'env', 'combined_training_flows.csv')
PT_PATH = os.path.join(ROOT, 'training', 'data', 'ciciot2023', 'device_gnn_hetero_sparse.pt')
ONNX_PATH = os.path.join(ROOT, 'backend', 'data', 'device_gnn_hetero.onnx')
NORM_PATH = os.path.join(ROOT, 'backend', 'data', 'device_gnn_norm.npz')

FEATURE_NAMES = ['flow_count', 'total_packets', 'total_bytes', 'avg_packets', 'avg_bytes',
                 'max_packets', 'unique_dst_ports', 'unique_dst_ips', 'unique_src_ports',
                 'unique_protocols', 'tcp_flags_mean', 'avg_flow_duration', 'internal_ratio']


def export_onnx(ckpt, onnx_path, norm_path, mean, std):
    """把稀疏权重转回稠密模型，导出 ONNX + 归一化参数（与 export_sparse_onnx.py 同款）。"""
    in_features = int(ckpt['feature_dim'])
    num_classes = int(ckpt['num_classes'])
    model = HeteroGATModel(
        input_features=in_features, num_classes=num_classes,
        hidden=64, num_heads=4, num_layers=2, dropout=0.3,
    )
    missing, unexpected = model.load_state_dict(ckpt['state_dict'], strict=False)
    assert not missing and not unexpected, f'权重键不匹配 missing={missing[:3]} unexpected={unexpected[:3]}'
    model.eval()

    n = 8
    dummy_x = torch.randn(n, in_features)
    dummy_adj = torch.ones(n, n)
    dummy_node_type = torch.zeros(n, dtype=torch.long)
    dummy_edge_type = torch.zeros(n, n, dtype=torch.long)
    dummy_edge_weight = torch.ones(n, n)
    torch.onnx.export(
        model,
        (dummy_x, dummy_adj, dummy_node_type, dummy_edge_type, dummy_edge_weight),
        onnx_path,
        input_names=['features', 'adjacency', 'node_type', 'edge_type', 'edge_weight'],
        output_names=['logits'],
        dynamic_axes={
            'features': {0: 'num_nodes'}, 'adjacency': {0: 'num_nodes', 1: 'num_nodes'},
            'node_type': {0: 'num_nodes'},
            'edge_type': {0: 'num_nodes', 1: 'num_nodes'},
            'edge_weight': {0: 'num_nodes', 1: 'num_nodes'}, 'logits': {0: 'num_nodes'},
        },
        opset_version=18,
        dynamo=False,
    )
    print(f'ONNX 导出 -> {onnx_path}')

    np.savez(
        norm_path,
        feature_mean=mean.astype(np.float32),
        feature_std=std.astype(np.float32),
        feature_names=np.array(FEATURE_NAMES),
    )
    print(f'归一化参数 -> {norm_path}')

    # ONNX 与 PyTorch 一致性
    import onnxruntime as ort
    sess = ort.InferenceSession(onnx_path, providers=['CPUExecutionProvider'])
    for k in [8, 16, 7]:
        x = np.random.randn(k, in_features).astype(np.float32)
        adj = np.random.randint(0, 2, (k, k)).astype(np.float32)
        adj = np.maximum(adj, adj.T)
        np.fill_diagonal(adj, 0.0)
        node_type = np.random.randint(0, 2, (k,)).astype(np.int64)
        edge_type = np.random.randint(-1, 7, (k, k)).astype(np.int64) * (adj > 0.5)
        edge_weight = np.random.rand(k, k).astype(np.float32) * adj
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
        print(f'  一致性 N={k:2d}: 最大误差 {diff:.2e} {"OK" if diff < 1e-4 else "FAIL"}')


def main():
    max_per_class = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    gamma = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    max_epochs = int(sys.argv[3]) if len(sys.argv) > 3 else 150

    env = pd.read_csv(ENV_CSV)
    cic = pd.read_csv(CICIOT_CSV)
    parts = [env, cic]
    if os.path.exists(REAL_CSV):
        real = pd.read_csv(REAL_CSV)
        parts.append(real)
        real_note = f' + 真实抓包 {len(real):,} 行'
    else:
        real_note = ''
    df = pd.concat(parts, ignore_index=True)
    df.to_csv(COMBINED_CSV, index=False)
    print(f'环境数据 {len(env):,} 行 + CICIoT2023 {len(cic):,} 行{real_note} '
          f'= 混合 {len(df):,} 行 -> {COMBINED_CSV}')

    X, edge_index, edge_type, edge_weight, labels, node_type, device_ips, mean, std = \
        build_graph_sparse(df, 60, max_per_class)
    train(X, edge_index, edge_type, edge_weight, labels, node_type, device_ips,
          PT_PATH, max_epochs=max_epochs, gamma=gamma)

    ckpt = torch.load(PT_PATH, map_location='cpu')
    export_onnx(ckpt, ONNX_PATH, NORM_PATH, mean, std)
    print('\n重训完成。验证: python backend/probe_gnn_six_devices.py')


if __name__ == '__main__':
    main()
