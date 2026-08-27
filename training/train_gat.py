"""
GAT (Graph Attention Network) Training Script — generic multi/binary class.

Flow-as-node: each mini-batch is an independent KNN graph over that batch's
flows. Works for any preprocessed dataset under training/data/<data_dir>:
    processed_nbaiot  -> 3-class (Normal/Mirai/Gafgyt)
    processed_cicids  -> binary (Normal/Attack)

Usage:
    python train_gat.py --data_dir processed_cicids --model_name best_model_gat_bin --epochs 30
"""
import os
import sys
import argparse
import pickle

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, confusion_matrix

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from backend.models.gat_model import create_gat_model, build_knn_graph, count_parameters

DATA_ROOT = os.path.join(os.path.dirname(__file__), 'data')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend', 'data')


def load_data(data_dir):
    train_csv = os.path.join(data_dir, 'train.csv')
    test_csv = os.path.join(data_dir, 'test.csv')
    weights_path = os.path.join(data_dir, 'class_weights.pkl')
    names_path = os.path.join(data_dir, 'label_names.pkl')

    train_df = pd.read_csv(train_csv)
    test_df = pd.read_csv(test_csv)

    X_train = train_df.iloc[:, :-1].values.astype(np.float32)
    y_train = train_df.iloc[:, -1].values.astype(np.int64)
    X_test = test_df.iloc[:, :-1].values.astype(np.float32)
    y_test = test_df.iloc[:, -1].values.astype(np.int64)

    class_weights = None
    if os.path.exists(weights_path):
        class_weights = torch.from_numpy(pickle.load(open(weights_path, 'rb')))

    label_names = ['Class%d' % i for i in range(len(np.unique(y_train)))]
    if os.path.exists(names_path):
        label_names = pickle.load(open(names_path, 'rb'))

    print(f'Train: {X_train.shape}, Test: {X_test.shape}')
    print(f'Feature dim: {X_train.shape[1]}, Classes: {len(np.unique(y_train))}')
    print(f'Per-class (train): {dict(zip(*np.unique(y_train, return_counts=True)))}')
    return X_train, y_train, X_test, y_test, class_weights, label_names


def train_epoch(model, loader, optimizer, criterion, device, k):
    model.train()
    total_loss, correct, total = 0, 0, 0
    for xb, yb in loader:
        xb, yb = xb.to(device), yb.to(device)
        adj = build_knn_graph(xb.cpu().numpy(), k=k)
        adj = torch.from_numpy(adj).to(device)

        optimizer.zero_grad()
        logits = model(xb, adj)
        loss = criterion(logits, yb)
        loss.backward()
        optimizer.step()

        total_loss += loss.item() * xb.size(0)
        correct += (logits.argmax(1) == yb).sum().item()
        total += xb.size(0)
    return total_loss / total, correct / total


@torch.no_grad()
def evaluate(model, loader, device, k):
    model.eval()
    all_preds, all_labels = [], []
    total_loss = 0
    for xb, yb in loader:
        xb, yb = xb.to(device), yb.to(device)
        adj = build_knn_graph(xb.cpu().numpy(), k=k)
        adj = torch.from_numpy(adj).to(device)
        logits = model(xb, adj)
        total_loss += nn.functional.cross_entropy(logits, yb).item() * xb.size(0)
        all_preds.extend(logits.argmax(1).cpu().tolist())
        all_labels.extend(yb.cpu().tolist())
    return all_preds, all_labels, total_loss / len(all_labels)


def export_onnx(model, weights_path, output_dir, in_features, model_name):
    try:
        model.load_state_dict(torch.load(weights_path, map_location='cpu'))
        model.eval()

        onnx_path = os.path.join(output_dir, f'{model_name}.onnx')
        dummy_x = torch.randn(8, in_features)
        dummy_adj = torch.ones(8, 8)

        torch.onnx.export(
            model,
            (dummy_x, dummy_adj),
            onnx_path,
            input_names=['features', 'adjacency'],
            output_names=['logits'],
            dynamic_axes={
                'features': {0: 'num_nodes'},
                'adjacency': {0: 'num_nodes', 1: 'num_nodes'},
                'logits': {0: 'num_nodes'},
            },
            opset_version=14,
        )
        print(f'ONNX exported to: {onnx_path}')
    except Exception as e:
        print(f'ONNX export failed: {e}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data_dir', default='processed_nbaiot')
    parser.add_argument('--model_name', default='best_model_gat')
    parser.add_argument('--epochs', type=int, default=30)
    parser.add_argument('--batch_size', type=int, default=128)
    parser.add_argument('--k', type=int, default=8, help='KNN neighbours per node')
    parser.add_argument('--lr', type=float, default=1e-3)
    parser.add_argument('--hidden', type=int, default=64)
    parser.add_argument('--num_heads', type=int, default=4)
    parser.add_argument('--patience', type=int, default=8)
    args = parser.parse_args()

    data_dir = os.path.join(DATA_ROOT, args.data_dir)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f'Device: {device}')

    X_train, y_train, X_test, y_test, class_weights, label_names = load_data(data_dir)
    in_features = X_train.shape[1]
    num_classes = len(np.unique(y_train))

    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    test_ds = TensorDataset(torch.from_numpy(X_test), torch.from_numpy(y_test))
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    test_loader = DataLoader(test_ds, batch_size=args.batch_size)

    model = create_gat_model(
        input_features=in_features, num_classes=num_classes,
        hidden=args.hidden, num_heads=args.num_heads,
    ).to(device)
    total, m = count_parameters(model)
    print(f'GAT: {total:,} params ({m:.3f}M)')

    optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    if class_weights is not None:
        class_weights = class_weights.to(device)
    criterion = nn.CrossEntropyLoss(weight=class_weights)

    best_f1, patience_counter = 0, 0
    best_model_path = os.path.join(OUTPUT_DIR, f'{args.model_name}.pt')
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f'\n{"="*60}\nTraining started\n{"="*60}')
    for epoch in range(1, args.epochs + 1):
        train_loss, train_acc = train_epoch(model, train_loader, optimizer, criterion, device, args.k)
        preds, labels, test_loss = evaluate(model, test_loader, device, args.k)
        scheduler.step()

        acc = accuracy_score(labels, preds)
        p, r, f1, _ = precision_recall_fscore_support(labels, preds, average='weighted', zero_division=0)
        f1_macro = precision_recall_fscore_support(labels, preds, average='macro', zero_division=0)[2]

        if f1_macro > best_f1:
            best_f1 = f1_macro
            patience_counter = 0
            torch.save(model.state_dict(), best_model_path)
        else:
            patience_counter += 1

        print(f'Epoch {epoch:3d}/{args.epochs} | '
              f'Train Loss {train_loss:.4f} Acc {train_acc:.4f} | '
              f'Test Acc {acc:.4f} F1(w) {f1:.4f} F1(m) {f1_macro:.4f} | '
              f'Best {best_f1:.4f} [{"·"*patience_counter}{" "*(args.patience-patience_counter)}]')

        if patience_counter >= args.patience:
            print(f'Early stopping at epoch {epoch}')
            break

    print(f'\n{">"*60}\nBest F1(macro): {best_f1:.4f}')
    print(f'Model saved: {best_model_path}')

    model.load_state_dict(torch.load(best_model_path, map_location=device))
    preds, labels, _ = evaluate(model, test_loader, device, args.k)
    print('\nPer-class metrics:')
    p, r, f1, sup = precision_recall_fscore_support(labels, preds, labels=list(range(num_classes)), zero_division=0)
    for i in range(num_classes):
        name = label_names[i] if i < len(label_names) else f'Class{i}'
        print(f'  {name:8s} P={p[i]:.4f} R={r[i]:.4f} F1={f1[i]:.4f} (n={sup[i]})')
    print('\nConfusion matrix (rows=true, cols=pred):')
    print(confusion_matrix(labels, preds))

    export_onnx(model, best_model_path, OUTPUT_DIR, in_features, args.model_name)


if __name__ == '__main__':
    main()
