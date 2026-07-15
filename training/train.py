"""
CNN+LSTM Model Training Script

Data: Expects CSV files in training/data/ with columns:
  - 21 feature columns (as named in FEATURE_NAMES)
  - 'label' column (0=Normal, 1=Mirai, 2=Gafgyt, 3=Hajime, 4=Other)

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
            X[i, [0, 3, 4, 18]] += np.random.uniform(0.5, 1.0, 4)  # Spike on flow/packet/syn
            y[i] = 1
        elif r < 0.83:  # 15% Gafgyt
            X[i] = np.random.normal(0.25, 0.25, 21)
            X[i, [1, 2, 12, 13]] += np.random.uniform(0.4, 0.9, 4)
            y[i] = 2
        elif r < 0.94:  # 11% Hajime
            X[i] = np.random.normal(0.28, 0.2, 21)
            X[i, [5, 6, 7]] += np.random.uniform(0.3, 0.8, 3)
            y[i] = 3
        else:  # 6% Other
            X[i] = np.random.normal(0.22, 0.28, 21)
            y[i] = 4

    # Clip to [0, 1]
    X = np.clip(X, 0, 1)
    return X, y


def load_or_generate_data(data_dir: str):
    """Try to load CSV data, fall back to synthetic."""
    train_csv = os.path.join(data_dir, 'train.csv')
    test_csv = os.path.join(data_dir, 'test.csv')

    if os.path.exists(train_csv) and os.path.exists(test_csv):
        print(f'Loading data from {data_dir}...')
        import pandas as pd
        train_df = pd.read_csv(train_csv)
        test_df = pd.read_csv(test_csv)

        # Assume last column is label
        X_train = train_df.iloc[:, :-1].values.astype(np.float32)
        y_train = train_df.iloc[:, -1].values.astype(np.int64)
        X_test = test_df.iloc[:, :-1].values.astype(np.float32)
        y_test = test_df.iloc[:, -1].values.astype(np.int64)
    else:
        print(f'No dataset found. Generating synthetic data ({data_dir})...')
        X, y = generate_synthetic_data(10000)
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, stratify=y, random_state=42)

    # Normalize
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train).astype(np.float32)
    X_test = scaler.transform(X_test).astype(np.float32)

    # Save scaler
    os.makedirs(os.path.join(os.path.dirname(__file__), '..', 'backend', 'data'), exist_ok=True)
    scaler_path = os.path.join(os.path.dirname(__file__), '..', 'backend', 'data', 'scaler.pkl')
    with open(scaler_path, 'wb') as f:
        pickle.dump(scaler, f)
    print(f'Scaler saved to {scaler_path}')

    return X_train, X_test, y_train, y_test


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
    parser.add_argument('--epochs', type=int, default=30)
    parser.add_argument('--batch_size', type=int, default=256)
    parser.add_argument('--lr', type=float, default=1e-3)
    parser.add_argument('--output_dir', default='../backend/data')
    args = parser.parse_args()

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f'Device: {device}')

    # Data
    X_train, X_test, y_train, y_test = load_or_generate_data(args.data_dir)
    print(f'Train: {X_train.shape[0]} samples, Test: {X_test.shape[0]} samples')
    print(f'Class distribution — Train: {np.bincount(y_train)}, Test: {np.bincount(y_test)}')

    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    test_ds = TensorDataset(torch.from_numpy(X_test), torch.from_numpy(y_test))
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    test_loader = DataLoader(test_ds, batch_size=args.batch_size)

    # Model
    model = create_model(input_features=21, num_classes=5).to(device)
    total_params, m_params = count_parameters(model)
    print(f'Model: {total_params:,} params ({m_params:.2f}M)')

    # Training setup
    optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    criterion = nn.CrossEntropyLoss()

    best_acc = 0
    best_model_path = os.path.join(args.output_dir, 'best_model.pt')
    os.makedirs(args.output_dir, exist_ok=True)

    print(f'\n{"="*60}')
    print(f'Training started at {datetime.now().strftime("%H:%M:%S")}')
    print(f'{"="*60}')

    for epoch in range(1, args.epochs + 1):
        train_loss, train_acc = train_epoch(model, train_loader, optimizer, criterion, device)
        test_loss, test_acc = evaluate(model, test_loader, criterion, device)
        scheduler.step()

        if test_acc > best_acc:
            best_acc = test_acc
            torch.save(model.state_dict(), best_model_path)

        if epoch % 5 == 0 or epoch == 1:
            print(f'Epoch {epoch:3d}/{args.epochs} | '
                  f'Train Loss: {train_loss:.4f}, Acc: {train_acc:.4f} | '
                  f'Test Loss: {test_loss:.4f}, Acc: {test_acc:.4f}')

    print(f'{">"*60}')
    print(f'Best test accuracy: {best_acc:.4f}')
    print(f'Model saved to: {best_model_path}')

    # Export to ONNX
    export_onnx(model, best_model_path, args.output_dir)


def export_onnx(model, weights_path, output_dir):
    """Export trained model to ONNX format."""
    try:
        model.load_state_dict(torch.load(weights_path, map_location='cpu'))
        model.eval()

        onnx_path = os.path.join(output_dir, 'best_model.onnx')
        dummy_input = torch.randn(1, 21)

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
