"""Train the real N-BaIoT CNN+LSTM model directly from the nine ZIP files.

This script intentionally does not extract the multi-gigabyte dataset.  It
streams CSV members from ZIP archives, performs a reproducible 21-feature
selection, constructs contiguous time windows, trains a three-class model and
exports artifacts used by the backend.

Classes:
    0 Normal/Benign
    1 Mirai
    2 Gafgyt

Example (PowerShell):
    python training/train.py --zip_dir D:\\datasets\\N-BaIoT --epochs 12
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import random
import re
import sys
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.feature_selection import SelectKBest, f_classif
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.preprocessing import StandardScaler
from torch.utils.data import DataLoader, TensorDataset

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.models.cnn_lstm import count_parameters, create_model

CLASS_NAMES = ["Normal", "Mirai", "Gafgyt"]
LABEL_BY_TOKEN = {"benign": 0, "mirai": 1, "gafgyt": 2}


@dataclass(frozen=True)
class CsvMember:
    zip_path: Path
    member_name: str
    device_id: int
    label: int


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def discover_members(zip_dir: Path) -> list[CsvMember]:
    members: list[CsvMember] = []
    for zip_path in sorted(zip_dir.glob("*.zip")):
        try:
            with zipfile.ZipFile(zip_path) as archive:
                for name in archive.namelist():
                    base = Path(name).name.lower()
                    match = re.match(r"(\d+)\.(benign|mirai|gafgyt)(?:\.|$)", base)
                    if not match or not base.endswith(".csv"):
                        continue
                    members.append(
                        CsvMember(
                            zip_path=zip_path,
                            member_name=name,
                            device_id=int(match.group(1)),
                            label=LABEL_BY_TOKEN[match.group(2)],
                        )
                    )
        except zipfile.BadZipFile as exc:
            raise RuntimeError(f"Invalid ZIP file: {zip_path}") from exc
    if not members:
        raise FileNotFoundError(f"No N-BaIoT CSV members found under {zip_dir}")
    return members


def read_member(member: CsvMember, *, usecols=None, nrows: int | None = None) -> pd.DataFrame:
    with zipfile.ZipFile(member.zip_path) as archive:
        with archive.open(member.member_name) as stream:
            return pd.read_csv(stream, usecols=usecols, nrows=nrows, low_memory=False)


def clean_array(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    values[~np.isfinite(values)] = np.nan
    if np.isnan(values).any():
        medians = np.nanmedian(values, axis=0)
        medians = np.where(np.isfinite(medians), medians, 0.0)
        row_idx, col_idx = np.where(np.isnan(values))
        values[row_idx, col_idx] = medians[col_idx]
    return values


def select_features(
    train_members: list[CsvMember],
    rows_per_file: int,
    count: int,
) -> tuple[list[str], list[str]]:
    frames: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    columns: list[str] | None = None

    for member in train_members:
        frame = read_member(member, nrows=rows_per_file)
        if columns is None:
            columns = frame.columns.tolist()
        elif frame.columns.tolist() != columns:
            raise ValueError(f"Feature schema mismatch: {member.member_name}")
        frames.append(clean_array(frame.to_numpy()))
        labels.append(np.full(len(frame), member.label, dtype=np.int64))

    assert columns is not None
    x = np.concatenate(frames, axis=0)
    y = np.concatenate(labels, axis=0)

    variances = np.nanvar(x, axis=0)
    variance_mask = variances > 1e-12
    x_var = x[:, variance_mask]
    var_columns = [name for name, keep in zip(columns, variance_mask) if keep]

    # Greedy correlation filtering.  The supervised selector then reduces the
    # remaining stable columns to exactly 21.
    correlation = np.corrcoef(x_var, rowvar=False)
    correlation = np.nan_to_num(np.abs(correlation), nan=0.0)
    keep = np.ones(len(var_columns), dtype=bool)
    for idx in range(len(var_columns)):
        if not keep[idx]:
            continue
        redundant = np.where(correlation[idx, idx + 1 :] > 0.95)[0] + idx + 1
        keep[redundant] = False
    x_filtered = x_var[:, keep]
    filtered_columns = [name for name, flag in zip(var_columns, keep) if flag]

    k = min(count, len(filtered_columns))
    selector = SelectKBest(score_func=f_classif, k=k)
    selector.fit(x_filtered, y)
    indices = np.flatnonzero(selector.get_support())
    # Put the highest-scoring selected features first for reproducibility.
    indices = indices[np.argsort(selector.scores_[indices])[::-1]]
    selected = [filtered_columns[index] for index in indices]
    if len(selected) < count:
        raise RuntimeError(f"Only {len(selected)} usable features remain; expected {count}")
    return selected, columns


def fit_scaler(
    train_members: list[CsvMember],
    selected_features: list[str],
    rows_attack: int,
    rows_benign: int,
) -> StandardScaler:
    scaler = StandardScaler()
    for member in train_members:
        nrows = rows_benign if member.label == 0 else rows_attack
        frame = read_member(member, usecols=selected_features, nrows=nrows)
        scaler.partial_fit(clean_array(frame.to_numpy()))
    return scaler


def windows_from_rows(rows: np.ndarray, sequence_length: int, stride: int) -> np.ndarray:
    if len(rows) < sequence_length:
        return np.empty((0, sequence_length, rows.shape[1]), dtype=np.float32)
    view = np.lib.stride_tricks.sliding_window_view(
        rows, window_shape=(sequence_length, rows.shape[1])
    )[:, 0, :, :]
    return np.ascontiguousarray(view[::stride], dtype=np.float32)


def build_datasets(
    members: list[CsvMember],
    selected_features: list[str],
    scaler: StandardScaler,
    sequence_length: int,
    rows_attack: int,
    rows_benign: int,
    train_devices: set[int],
    test_devices: set[int],
    val_fraction: float,
    seed: int,
    max_train_per_class: int,
    max_val_per_class: int,
    max_test_per_class: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict]:
    rng = np.random.default_rng(seed)
    split_data: dict[str, dict[int, list[np.ndarray]]] = {
        "train": defaultdict(list),
        "val": defaultdict(list),
        "test": defaultdict(list),
    }
    source_counts = Counter()

    for index, member in enumerate(members, start=1):
        if member.device_id not in train_devices | test_devices:
            continue
        nrows = rows_benign if member.label == 0 else rows_attack
        frame = read_member(member, usecols=selected_features, nrows=nrows)
        rows = clean_array(frame.to_numpy())
        rows = scaler.transform(rows).astype(np.float32)

        # Benign files are much fewer than attack files, so a smaller stride is
        # used to keep the training set balanced without synthetic SMOTE rows.
        stride = max(1, sequence_length // 4) if member.label == 0 else sequence_length
        sequences = windows_from_rows(rows, sequence_length, stride)
        if len(sequences) == 0:
            continue

        if member.device_id in test_devices:
            split_data["test"][member.label].append(sequences)
            source_counts[("test", member.label)] += len(sequences)
        else:
            cut = max(1, int(len(sequences) * (1.0 - val_fraction)))
            split_data["train"][member.label].append(sequences[:cut])
            split_data["val"][member.label].append(sequences[cut:])
            source_counts[("train", member.label)] += cut
            source_counts[("val", member.label)] += len(sequences) - cut

        print(
            f"[{index:02d}/{len(members):02d}] device={member.device_id} "
            f"class={CLASS_NAMES[member.label]:7s} windows={len(sequences):5d} "
            f"file={member.member_name}",
            flush=True,
        )

    caps = {
        "train": max_train_per_class,
        "val": max_val_per_class,
        "test": max_test_per_class,
    }

    outputs: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    final_counts: dict[str, dict[str, int]] = {}
    for split in ("train", "val", "test"):
        x_parts: list[np.ndarray] = []
        y_parts: list[np.ndarray] = []
        final_counts[split] = {}
        for label in range(len(CLASS_NAMES)):
            arrays = split_data[split].get(label, [])
            if not arrays:
                raise RuntimeError(f"Split {split} has no samples for {CLASS_NAMES[label]}")
            x_class = np.concatenate(arrays, axis=0)
            cap = caps[split]
            if cap > 0 and len(x_class) > cap:
                chosen = rng.choice(len(x_class), size=cap, replace=False)
                x_class = x_class[chosen]
            x_parts.append(x_class)
            y_parts.append(np.full(len(x_class), label, dtype=np.int64))
            final_counts[split][CLASS_NAMES[label]] = len(x_class)

        x_split = np.concatenate(x_parts, axis=0)
        y_split = np.concatenate(y_parts, axis=0)
        order = rng.permutation(len(y_split))
        outputs[split] = (x_split[order], y_split[order])

    return (
        *outputs["train"],
        *outputs["val"],
        *outputs["test"],
        {"available_windows": {str(k): v for k, v in source_counts.items()}, "final_counts": final_counts},
    )


class FocalLoss(nn.Module):
    def __init__(self, gamma: float = 2.0):
        super().__init__()
        self.gamma = gamma

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        ce = nn.functional.cross_entropy(logits, targets, reduction="none")
        pt = torch.exp(-ce)
        return torch.mean(((1.0 - pt) ** self.gamma) * ce)


def run_epoch(model, loader, criterion, device, optimizer=None):
    training = optimizer is not None
    model.train(training)
    total_loss = 0.0
    correct = 0
    count = 0
    predictions: list[np.ndarray] = []
    targets: list[np.ndarray] = []

    for features, labels in loader:
        features = features.to(device)
        labels = labels.to(device)
        if training:
            optimizer.zero_grad(set_to_none=True)
        logits = model(features)
        loss = criterion(logits, labels)
        if training:
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
            optimizer.step()
        batch_size = labels.size(0)
        total_loss += loss.item() * batch_size
        predicted = logits.argmax(dim=1)
        correct += (predicted == labels).sum().item()
        count += batch_size
        predictions.append(predicted.detach().cpu().numpy())
        targets.append(labels.detach().cpu().numpy())

    return {
        "loss": total_loss / max(count, 1),
        "accuracy": correct / max(count, 1),
        "predictions": np.concatenate(predictions),
        "targets": np.concatenate(targets),
    }


def save_plots(history: list[dict], matrix: np.ndarray, output_dir: Path) -> None:
    epochs = [row["epoch"] for row in history]
    plt.figure(figsize=(8, 5))
    plt.plot(epochs, [row["train_loss"] for row in history], label="Train loss")
    plt.plot(epochs, [row["val_loss"] for row in history], label="Validation loss")
    plt.xlabel("Epoch")
    plt.ylabel("Loss")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_dir / "training_loss.png", dpi=160)
    plt.close()

    plt.figure(figsize=(8, 5))
    plt.plot(epochs, [row["train_accuracy"] for row in history], label="Train accuracy")
    plt.plot(epochs, [row["val_accuracy"] for row in history], label="Validation accuracy")
    plt.xlabel("Epoch")
    plt.ylabel("Accuracy")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_dir / "training_accuracy.png", dpi=160)
    plt.close()

    plt.figure(figsize=(6, 5))
    plt.imshow(matrix)
    plt.xticks(range(len(CLASS_NAMES)), CLASS_NAMES)
    plt.yticks(range(len(CLASS_NAMES)), CLASS_NAMES)
    plt.xlabel("Predicted")
    plt.ylabel("Actual")
    for row in range(matrix.shape[0]):
        for col in range(matrix.shape[1]):
            plt.text(col, row, str(matrix[row, col]), ha="center", va="center")
    plt.tight_layout()
    plt.savefig(output_dir / "confusion_matrix.png", dpi=160)
    plt.close()


def export_onnx(model, sequence_length: int, feature_count: int, output_path: Path) -> None:
    model_cpu = model.to("cpu").eval()
    dummy = torch.randn(1, sequence_length, feature_count)
    torch.onnx.export(
        model_cpu,
        dummy,
        str(output_path),
        input_names=["features"],
        output_names=["logits"],
        dynamic_axes={"features": {0: "batch_size"}, "logits": {0: "batch_size"}},
        opset_version=17,
        dynamo=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train CNN+LSTM from N-BaIoT ZIP files")
    parser.add_argument("--zip_dir", type=Path, required=True)
    parser.add_argument("--output_dir", type=Path, default=PROJECT_ROOT / "backend" / "data")
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch_size", type=int, default=512)
    parser.add_argument("--learning_rate", type=float, default=1e-3)
    parser.add_argument("--sequence_length", type=int, default=16)
    parser.add_argument("--feature_count", type=int, default=21)
    parser.add_argument("--feature_sample_rows", type=int, default=600)
    parser.add_argument("--rows_attack", type=int, default=12000)
    parser.add_argument("--rows_benign", type=int, default=50000)
    parser.add_argument("--max_train_per_class", type=int, default=18000)
    parser.add_argument("--max_val_per_class", type=int, default=3000)
    parser.add_argument("--max_test_per_class", type=int, default=5000)
    parser.add_argument("--test_devices", default="8,9")
    parser.add_argument("--val_fraction", type=float, default=0.15)
    parser.add_argument("--patience", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    started = time.time()
    seed_everything(args.seed)
    torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    members = discover_members(args.zip_dir)
    test_devices = {int(value) for value in args.test_devices.split(",") if value.strip()}
    all_devices = {member.device_id for member in members}
    train_devices = all_devices - test_devices
    train_members = [member for member in members if member.device_id in train_devices]

    print(f"Discovered {len(members)} CSV files from devices {sorted(all_devices)}")
    print(f"Train/validation devices: {sorted(train_devices)}; test devices: {sorted(test_devices)}")

    print("\n[1/5] Selecting 21 features...")
    selected_features, original_features = select_features(
        train_members, args.feature_sample_rows, args.feature_count
    )
    print("Selected features:")
    for number, name in enumerate(selected_features, start=1):
        print(f"  {number:02d}. {name}")

    print("\n[2/5] Fitting scaler...")
    scaler = fit_scaler(
        train_members,
        selected_features,
        args.rows_attack,
        args.rows_benign,
    )

    print("\n[3/5] Building sequence datasets...")
    x_train, y_train, x_val, y_val, x_test, y_test, data_stats = build_datasets(
        members=members,
        selected_features=selected_features,
        scaler=scaler,
        sequence_length=args.sequence_length,
        rows_attack=args.rows_attack,
        rows_benign=args.rows_benign,
        train_devices=train_devices,
        test_devices=test_devices,
        val_fraction=args.val_fraction,
        seed=args.seed,
        max_train_per_class=args.max_train_per_class,
        max_val_per_class=args.max_val_per_class,
        max_test_per_class=args.max_test_per_class,
    )
    print("Final split counts:", data_stats["final_counts"])

    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(x_train), torch.from_numpy(y_train)),
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=0,
    )
    val_loader = DataLoader(
        TensorDataset(torch.from_numpy(x_val), torch.from_numpy(y_val)),
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=0,
    )
    test_loader = DataLoader(
        TensorDataset(torch.from_numpy(x_test), torch.from_numpy(y_test)),
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=0,
    )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = create_model(args.feature_count, len(CLASS_NAMES)).to(device)
    total_params, million_params = count_parameters(model)
    print(f"\n[4/5] Training on {device}; parameters={total_params:,} ({million_params:.3f}M)")

    criterion = FocalLoss(gamma=2.0)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.learning_rate, weight_decay=1e-4
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    best_val = -1.0
    best_state = None
    no_improvement = 0
    history: list[dict] = []

    for epoch in range(1, args.epochs + 1):
        train_result = run_epoch(model, train_loader, criterion, device, optimizer)
        with torch.no_grad():
            val_result = run_epoch(model, val_loader, criterion, device)
        scheduler.step()
        row = {
            "epoch": epoch,
            "train_loss": train_result["loss"],
            "train_accuracy": train_result["accuracy"],
            "val_loss": val_result["loss"],
            "val_accuracy": val_result["accuracy"],
        }
        history.append(row)
        print(
            f"Epoch {epoch:02d}/{args.epochs} | "
            f"train loss={row['train_loss']:.4f} acc={row['train_accuracy']:.4f} | "
            f"val loss={row['val_loss']:.4f} acc={row['val_accuracy']:.4f}"
        )
        if val_result["accuracy"] > best_val + 1e-5:
            best_val = val_result["accuracy"]
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            no_improvement = 0
        else:
            no_improvement += 1
            if no_improvement >= args.patience:
                print(f"Early stopping after {epoch} epochs")
                break

    assert best_state is not None
    model.load_state_dict(best_state)
    model.to(device)

    print("\n[5/5] Evaluating held-out devices and exporting artifacts...")
    with torch.no_grad():
        test_result = run_epoch(model, test_loader, criterion, device)
    report = classification_report(
        test_result["targets"],
        test_result["predictions"],
        labels=list(range(len(CLASS_NAMES))),
        target_names=CLASS_NAMES,
        output_dict=True,
        zero_division=0,
    )
    matrix = confusion_matrix(
        test_result["targets"],
        test_result["predictions"],
        labels=list(range(len(CLASS_NAMES))),
    )

    checkpoint = {
        "state_dict": best_state,
        "input_features": args.feature_count,
        "sequence_length": args.sequence_length,
        "num_classes": len(CLASS_NAMES),
        "class_names": CLASS_NAMES,
        "selected_features": selected_features,
    }
    torch.save(checkpoint, args.output_dir / "best_model.pt")

    # TorchScript is always exported because it requires no additional ONNX
    # package and can be loaded directly by the backend on Windows/Linux.
    scripted_model = torch.jit.trace(
        model.to("cpu").eval(),
        torch.randn(1, args.sequence_length, args.feature_count),
    )
    scripted_model.save(str(args.output_dir / "best_model.ts"))

    with open(args.output_dir / "scaler.pkl", "wb") as handle:
        pickle.dump(scaler, handle)
    scaler_payload = {
        "type": "StandardScaler",
        "n_features_in": int(scaler.n_features_in_),
        "mean": np.asarray(scaler.mean_, dtype=float).tolist(),
        "scale": np.asarray(scaler.scale_, dtype=float).tolist(),
        "var": np.asarray(scaler.var_, dtype=float).tolist(),
    }
    (args.output_dir / "scaler.json").write_text(
        json.dumps(scaler_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    schema = {
        "dataset": "N-BaIoT",
        "original_feature_count": len(original_features),
        "selected_feature_count": len(selected_features),
        "selected_features": selected_features,
        "sequence_length": args.sequence_length,
        "input_shape": [None, args.sequence_length, len(selected_features)],
        "classes": {str(index): name for index, name in enumerate(CLASS_NAMES)},
        "test_devices": sorted(test_devices),
    }
    (args.output_dir / "feature_schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    metadata = {
        **schema,
        "model": "Lightweight temporal CNN + LSTM + attention",
        "parameter_count": total_params,
        "best_validation_accuracy": best_val,
        "test_accuracy": test_result["accuracy"],
        "test_loss": test_result["loss"],
        "classification_report": report,
        "confusion_matrix": matrix.tolist(),
        "data_stats": data_stats,
        "history": history,
        "training_seconds": round(time.time() - started, 2),
        "seed": args.seed,
        "note": "Test data are held-out devices 8 and 9; this is a sampled CPU training run.",
    }
    (args.output_dir / "training_report.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pd.DataFrame(report).transpose().to_csv(args.output_dir / "classification_report.csv")
    pd.DataFrame(matrix, index=CLASS_NAMES, columns=CLASS_NAMES).to_csv(
        args.output_dir / "confusion_matrix.csv"
    )
    pd.DataFrame(history).to_csv(args.output_dir / "training_history.csv", index=False)
    (args.output_dir / "selected_features.txt").write_text(
        "\n".join(selected_features) + "\n", encoding="utf-8"
    )
    save_plots(history, matrix, args.output_dir)

    try:
        export_onnx(
            model,
            args.sequence_length,
            len(selected_features),
            args.output_dir / "best_model.onnx",
        )
        print("ONNX export succeeded")
    except Exception as exc:
        print(f"ONNX export failed: {exc}")

    print(json.dumps({"test_accuracy": test_result["accuracy"], "report": report}, indent=2))
    print(f"Artifacts saved under {args.output_dir}")


if __name__ == "__main__":
    main()
