"""
CNN+LSTM Model Training Script

Data: Expects CSV files in training/data/ with columns:
  - 21 feature columns (as named in FEATURE_NAMES)
  - 'label' column (0=Normal, 1=Mirai, 2=Gafgyt, 3=Other)

Usage:
  python train.py --data_dir ./data --epochs 50 --batch_size 256

For MVP: If no real dataset is available, synthetic data is generated
to verify the training pipeline works.
"""
import os
import sys
import argparse
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import pickle
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.models.cnn_lstm import create_model, count_parameters


def generate_synthetic_data(n_samples: int = 10000):
    """Generate synthetic training data for pipeline verification."""
    np.random.seed(42)
    X = np.zeros((n_samples, 21), dtype=np.float32)
    y = np.zeros(n_samples, dtype=np.int64)

    for i in range(n_samples):
        r = np.random.random()
        if r < 0.50:  # 50% Normal
            X[i] = np.random.normal(0.3, 0.15, 21)
            y[i] = 0
        elif r < 0.68:  # 18% Mirai
            X[i] = np.random.normal(0.2, 0.3, 21)
            X[i, [0, 3, 4, 18]] += np.random.uniform(0.5, 1.0, 4)
            y[i] = 1
        elif r < 0.83:  # 15% Gafgyt
            X[i] = np.random.normal(0.25, 0.25, 21)
            X[i, [1, 2, 12, 13]] += np.random.uniform(0.4, 0.9, 4)
            y[i] = 2
        else:  # 17% Other
            X[i] = np.random.normal(0.22, 0.28, 21)
            y[i] = 3

    # Clip to [0, 1]
    X = np.clip(X, 0, 1)
    return X, y


def load_data(data_dir: str):
    """Load preprocessed CSV data."""
    train_csv = os.path.join(data_dir, 'processed', 'train.csv')
    test_csv = os.path.join(data_dir, 'processed', 'test.csv')
    weights_path = os.path.join(data_dir, 'processed', 'class_weights.pkl')

    if not (os.path.exists(train_csv) and os.path.exists(test_csv)):
        print(f'未找到预处理数据，请先运行: python training/preprocess.py')
        sys.exit(1)

    print(f'加载预处理数据: {data_dir}/processed/')
    import pandas as pd
    train_df = pd.read_csv(train_csv)
    test_df = pd.read_csv(test_csv)

    X_train = train_df.iloc[:, :-1].values.astype(np.float32)
    y_train = train_df.iloc[:, -1].values.astype(np.int64)
    X_test = test_df.iloc[:, :-1].values.astype(np.float32)
    y_test = test_df.iloc[:, -1].values.astype(np.int64)

    # 类别权重
    class_weights = None
    if os.path.exists(weights_path):
        class_weights = torch.from_numpy(pickle.load(open(weights_path, 'rb')))
        print(f'类别权重: {class_weights.numpy().round(4)}')

    print(f'特征维度: {X_train.shape[1]}, 类别数: {len(np.unique(y_train))}')
    print(f'各类样本: {dict(zip(*np.unique(y_train, return_counts=True)))}')

    return X_train, X_test, y_train, y_test, class_weights


def train_epoch(model, loader, optimizer, criterion, device):
    model.train()
    total_loss, correct, total = 0, 0, 0
    for xb, yb in loader:
        xb, yb = xb.to(device), yb.to(device)
        optimizer.zero_grad()
        logits = model(xb)
        loss = criterion(logits, yb)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * xb.size(0)
        correct += (logits.argmax(1) == yb).sum().item()
        total += xb.size(0)
    return total_loss / total, correct / total


@torch.no_grad()
def evaluate(model, loader, criterion, device):
    model.eval()
    total_loss, correct, total = 0, 0, 0
    for xb, yb in loader:
        xb, yb = xb.to(device), yb.to(device)
        logits = model(xb)
        loss = criterion(logits, yb)
        total_loss += loss.item() * xb.size(0)
        correct += (logits.argmax(1) == yb).sum().item()
        total += xb.size(0)
    return total_loss / total, correct / total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data_dir', default='./data')
    parser.add_argument('--epochs', type=int, default=80)
    parser.add_argument('--batch_size', type=int, default=256)
    parser.add_argument('--lr', type=float, default=1e-3)
    parser.add_argument('--output_dir', default='../backend/data')
    args = parser.parse_args()

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f'Device: {device}')

    # Data
    X_train, X_test, y_train, y_test, class_weights = load_data(args.data_dir)
    in_features = X_train.shape[1]
    num_classes = len(np.unique(y_train))
    print(f'Train: {X_train.shape[0]} samples, Test: {X_test.shape[0]} samples')

    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    test_ds = TensorDataset(torch.from_numpy(X_test), torch.from_numpy(y_test))
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    test_loader = DataLoader(test_ds, batch_size=args.batch_size)

    # Model
    model = create_model(input_features=in_features, num_classes=num_classes).to(device)
    total_params, m_params = count_parameters(model)
    print(f'Model: {total_params:,} params ({m_params:.2f}M)')

    optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    if class_weights is not None:
        class_weights = class_weights.to(device)
    criterion = nn.CrossEntropyLoss(weight=class_weights)

    best_acc = 0
    best_f1 = 0
    patience_counter = 0
    best_model_path = os.path.join(args.output_dir, 'best_model.pt')
    os.makedirs(args.output_dir, exist_ok=True)

    print(f'\n{"="*60}')
    print(f'Training started at {datetime.now().strftime("%H:%M:%S")}')
    print(f'{"="*60}')

    for epoch in range(1, args.epochs + 1):
        train_loss, train_acc = train_epoch(model, train_loader, optimizer, criterion, device)
        test_loss, test_acc = evaluate(model, test_loader, criterion, device)
        scheduler.step()

        # 每轮计算完整指标
        model.eval()
        with torch.no_grad():
            all_preds, all_labels = [], []
            for xb, yb in test_loader:
                logits = model(xb.to(device))
                all_preds.extend(logits.argmax(1).cpu().tolist())
                all_labels.extend(yb.tolist())
        from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
        epoch_prec = precision_score(all_labels, all_preds, average='weighted', zero_division=0)
        epoch_rec = recall_score(all_labels, all_preds, average='weighted', zero_division=0)
        epoch_f1 = f1_score(all_labels, all_preds, average='weighted', zero_division=0)

        # 早停：F1连续10轮不上升
        if epoch_f1 > best_f1:
            best_f1 = epoch_f1
            best_acc = test_acc
            patience_counter = 0
            torch.save(model.state_dict(), best_model_path)
        else:
            patience_counter += 1

        print(f'Epoch {epoch:3d}/{args.epochs} | '
              f'Train Loss: {train_loss:.4f} Acc: {train_acc:.4f} | '
              f'Test P:{epoch_prec:.4f} R:{epoch_rec:.4f} F1:{epoch_f1:.4f} | '
              f'Best F1:{best_f1:.4f} [{"·"*patience_counter}{" "*(10-patience_counter)}]')

        if patience_counter >= 10:
            print(f'Early stopping at epoch {epoch} (F1 not improved for 10 epochs)')
            break

    print(f'{">"*60}')
    print(f'Best accuracy: {best_acc:.4f} | Best F1: {best_f1:.4f}')
    print(f'Model saved to: {best_model_path}')

    # Export to ONNX
    export_onnx(model, best_model_path, args.output_dir, in_features)


def export_onnx(model, weights_path, output_dir, in_features):
    """Export trained model to ONNX format."""
    try:
        model.load_state_dict(torch.load(weights_path, map_location='cpu'))
        model.eval()

        onnx_path = os.path.join(output_dir, 'best_model.onnx')
        dummy_input = torch.randn(1, in_features)

        torch.onnx.export(
            model,
            dummy_input,
            onnx_path,
            input_names=['features'],
            output_names=['logits'],
            dynamic_axes={
                'features': {0: 'batch_size'},
                'logits': {0: 'batch_size'},
            },
            opset_version=14,
        )
        print(f'ONNX model exported to: {onnx_path}')
    except Exception as e:
        print(f'ONNX export failed: {e}')


if __name__ == '__main__':
    main()
