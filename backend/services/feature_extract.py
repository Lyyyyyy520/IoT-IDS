"""Feature preparation for the trained N-BaIoT CNN+LSTM model.

The real model uses 21 selected N-BaIoT statistical features over 16
consecutive records.  ``scaler.json`` is preferred because it is independent
of the local scikit-learn version; the original ``scaler.pkl`` remains as a
compatibility fallback.
"""
from __future__ import annotations

import json
import os
import pickle
from pathlib import Path
from typing import List, Optional

import numpy as np

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_SCHEMA_PATH = _DATA_DIR / "feature_schema.json"
_DEFAULT_SCALER_JSON = _DATA_DIR / "scaler.json"
_DEFAULT_SCALER_PKL = _DATA_DIR / "scaler.pkl"


def _read_schema() -> dict:
    try:
        return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


_SCHEMA = _read_schema()
FEATURE_NAMES = _SCHEMA.get("selected_features", [])
FEATURE_DIM = int(_SCHEMA.get("selected_feature_count", len(FEATURE_NAMES) or 21))
SEQUENCE_LENGTH = int(_SCHEMA.get("sequence_length", 16))


class JsonStandardScaler:
    """Minimal StandardScaler transform loaded from a portable JSON file."""

    def __init__(self, payload: dict):
        self.mean_ = np.asarray(payload["mean"], dtype=np.float64)
        self.scale_ = np.asarray(payload["scale"], dtype=np.float64)
        self.scale_ = np.where(self.scale_ == 0.0, 1.0, self.scale_)
        self.n_features_in_ = int(payload.get("n_features_in", len(self.mean_)))
        if len(self.mean_) != self.n_features_in_ or len(self.scale_) != self.n_features_in_:
            raise ValueError("scaler.json 的参数长度不一致")

    def transform(self, rows: np.ndarray) -> np.ndarray:
        rows = np.asarray(rows, dtype=np.float64)
        if rows.shape[-1] != self.n_features_in_:
            raise ValueError(
                f"标准化器需要 {self.n_features_in_} 个特征，实际收到 {rows.shape[-1]} 个"
            )
        return (rows - self.mean_) / self.scale_


class FeatureExtractor:
    def __init__(self, scaler_path: Optional[str] = None):
        self.feature_names = list(FEATURE_NAMES)
        self.sequence_length = SEQUENCE_LENGTH
        self.scaler = None
        self.scaler_path: Optional[Path] = None
        self.load_error = ""
        self._load_scaler(scaler_path)

    def _load_scaler(self, scaler_path: Optional[str]) -> None:
        candidates: List[Path]
        if scaler_path:
            requested = Path(scaler_path)
            candidates = [requested]
            if requested.suffix.lower() == ".pkl":
                candidates.insert(0, requested.with_suffix(".json"))
        else:
            candidates = [_DEFAULT_SCALER_JSON, _DEFAULT_SCALER_PKL]

        errors = []
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                if candidate.suffix.lower() == ".json":
                    payload = json.loads(candidate.read_text(encoding="utf-8"))
                    self.scaler = JsonStandardScaler(payload)
                else:
                    with candidate.open("rb") as handle:
                        self.scaler = pickle.load(handle)
                self.scaler_path = candidate
                return
            except Exception as exc:
                errors.append(f"{candidate.name}: {type(exc).__name__}: {exc}")

        if errors:
            self.load_error = "; ".join(errors)
        else:
            self.load_error = "未找到 scaler.json 或 scaler.pkl"

    def validate_bundle(self) -> None:
        if not self.feature_names or len(self.feature_names) != FEATURE_DIM:
            raise RuntimeError("feature_schema.json 缺失、损坏或特征数不一致")
        if self.scaler is None:
            raise RuntimeError(f"模型标准化器不可用：{self.load_error}")
        scaler_features = getattr(self.scaler, "n_features_in_", FEATURE_DIM)
        if int(scaler_features) != FEATURE_DIM:
            raise RuntimeError(
                f"标准化器特征数为 {scaler_features}，模型需要 {FEATURE_DIM}"
            )

    def _scale(self, rows: np.ndarray) -> np.ndarray:
        self.validate_bundle()
        rows = np.asarray(rows, dtype=np.float64)
        if rows.ndim != 2 or rows.shape[1] != FEATURE_DIM:
            raise ValueError(f"需要二维 ({FEATURE_DIM} 特征) 数据，实际为 {rows.shape}")
        rows[~np.isfinite(rows)] = np.nan
        if np.isnan(rows).any():
            medians = np.nanmedian(rows, axis=0)
            medians = np.where(np.isfinite(medians), medians, 0.0)
            row_idx, col_idx = np.where(np.isnan(rows))
            rows[row_idx, col_idx] = medians[col_idx]
        rows = self.scaler.transform(rows)
        return np.asarray(rows, dtype=np.float32)

    def extract_from_flow(self, flow_data: dict) -> np.ndarray:
        self.validate_bundle()
        row = np.array(
            [float(flow_data.get(name, 0.0)) for name in self.feature_names],
            dtype=np.float64,
        ).reshape(1, -1)
        return self._scale(row).reshape(-1)

    def extract_from_csv(
        self,
        csv_path: str,
        max_sequences: int = 2000,
        stride: Optional[int] = None,
    ) -> List[np.ndarray]:
        import pandas as pd

        self.validate_bundle()
        stride = int(stride or self.sequence_length)
        if stride <= 0:
            raise ValueError("stride 必须大于 0")
        max_rows = max_sequences * stride + self.sequence_length
        try:
            frame = pd.read_csv(
                csv_path,
                usecols=self.feature_names,
                nrows=max_rows,
                low_memory=False,
            )
        except ValueError as exc:
            try:
                header = pd.read_csv(csv_path, nrows=0).columns.tolist()
                missing = [name for name in self.feature_names if name not in header]
            except Exception:
                missing = []
            detail = f"，缺少字段：{', '.join(missing[:8])}" if missing else ""
            raise ValueError(f"CSV 与 N-BaIoT 115 维字段结构不兼容{detail}") from exc

        rows = self._scale(frame.to_numpy())
        if len(rows) < self.sequence_length:
            return []
        view = np.lib.stride_tricks.sliding_window_view(
            rows, window_shape=(self.sequence_length, rows.shape[1])
        )[:, 0, :, :]
        return [
            np.ascontiguousarray(sequence, dtype=np.float32)
            for sequence in view[::stride][:max_sequences]
        ]

    def extract_from_pcap(self, path: str) -> List[np.ndarray]:
        # N-BaIoT CSV is the real supported input. The legacy PCAP route is
        # retained as a UI demonstration until exact 115-feature incremental
        # extraction is implemented.
        if Path(path).suffix.lower() == ".csv":
            return self.extract_from_csv(path)
        return self._mock_extract(num_samples=50)

    def _mock_extract(self, num_samples: int = 1) -> List[np.ndarray]:
        self.validate_bundle()
        rng = np.random.default_rng(42)
        return [
            rng.normal(
                0.0, 1.0, size=(self.sequence_length, FEATURE_DIM)
            ).astype(np.float32)
            for _ in range(num_samples)
        ]


def save_scaler(scaler, path: str):
    """Save both the original pickle and a portable JSON representation."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        pickle.dump(scaler, handle)

    json_target = target.with_suffix(".json")
    payload = {
        "type": "StandardScaler",
        "n_features_in": int(getattr(scaler, "n_features_in_", len(scaler.mean_))),
        "mean": np.asarray(scaler.mean_, dtype=float).tolist(),
        "scale": np.asarray(scaler.scale_, dtype=float).tolist(),
        "var": np.asarray(getattr(scaler, "var_", np.square(scaler.scale_)), dtype=float).tolist(),
    }
    json_target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
