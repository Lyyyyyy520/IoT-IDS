"""
Export trained GAT model (.pt) to ONNX, and copy the scaler next to it.

Run with UTF-8 IO (Windows Chinese locale):  PYTHONIOENCODING=utf-8

Usage:
    python export_gat_onnx.py
"""
import os
import sys
import shutil
import pickle

import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from backend.models.gat_model import create_gat_model

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PT_PATH = os.path.join(ROOT, 'backend', 'data', 'best_model_gat.pt')
ONNX_PATH = os.path.join(ROOT, 'backend', 'data', 'best_model_gat.onnx')
SCALER_SRC = os.path.join(ROOT, 'training', 'data', 'processed_nbaiot', 'scaler.pkl')
SCALER_DST = os.path.join(ROOT, 'backend', 'data', 'scaler_gat.pkl')


def main():
    # Determine feature dim from the scaler (n_features_in_)
    with open(SCALER_SRC, 'rb') as f:
        scaler = pickle.load(f)
    in_features = int(scaler.n_features_in_)
    num_classes = 3
    print(f'Feature dim: {in_features}, Classes: {num_classes}')

    model = create_gat_model(input_features=in_features, num_classes=num_classes)
    model.load_state_dict(torch.load(PT_PATH, map_location='cpu'))
    model.eval()

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
    print(f'ONNX exported -> {ONNX_PATH}')

    shutil.copy(SCALER_SRC, SCALER_DST)
    print(f'Scaler copied -> {SCALER_DST}')


if __name__ == '__main__':
    main()
