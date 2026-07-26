"""Unified inference engine for TorchScript, PyTorch checkpoints and ONNX.

The trained N-BaIoT CNN+LSTM model consumes a sequence shaped ``(T, 21)``.
Legacy ONNX models that consume one ``(21,)`` feature row remain supported so
that switching models does not break the existing project.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_SCHEMA_PATH = _DATA_DIR / "feature_schema.json"
_USER_MODEL_DIR = _DATA_DIR / "models"
_DEFAULT_TS_PATH = _DATA_DIR / "best_model.ts"
_DEFAULT_PT_PATH = _DATA_DIR / "best_model.pt"
_DEFAULT_ONNX_PATH = _DATA_DIR / "best_model.onnx"
_MODEL_CONFIG_PATH = _DATA_DIR / "model_config.json"


def _load_schema() -> dict:
    try:
        return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


_SCHEMA = _load_schema()
_DEFAULT_CLASS_MAP = _SCHEMA.get(
    "classes", {"0": "Normal", "1": "Mirai", "2": "Gafgyt"}
)
DEFAULT_CLASS_NAMES = [
    _DEFAULT_CLASS_MAP[str(index)] for index in range(len(_DEFAULT_CLASS_MAP))
]
DEFAULT_SEQUENCE_LENGTH = int(_SCHEMA.get("sequence_length", 16))
DEFAULT_FEATURE_COUNT = int(_SCHEMA.get("selected_feature_count", 21))

RISK_THRESHOLDS = {"critical": 0.95, "high": 0.85, "medium": 0.70}
_SUPPORTED_EXTENSIONS = {".ts", ".torchscript", ".ptl", ".pt", ".pth", ".onnx"}


class InferenceEngine:
    """Load and run compatible IoT IDS models with clear validation errors."""

    def __init__(self, model_path: Optional[str] = None):
        self.session = None
        self.torch_model = None
        self.backend: Optional[str] = None
        self.model_loaded = False
        self.model_path: Optional[str] = None
        self.last_error = ""
        self.class_names = list(DEFAULT_CLASS_NAMES)
        self.sequence_length = DEFAULT_SEQUENCE_LENGTH
        self.feature_count = DEFAULT_FEATURE_COUNT
        self.input_rank = 3
        if model_path:
            self.load(model_path)

    def _reset_runtime(self) -> None:
        self.session = None
        self.torch_model = None
        self.backend = None
        self.model_loaded = False
        self.model_path = None
        self.last_error = ""
        self.class_names = list(DEFAULT_CLASS_NAMES)
        self.sequence_length = DEFAULT_SEQUENCE_LENGTH
        self.feature_count = DEFAULT_FEATURE_COUNT
        self.input_rank = 3

    def load(self, model_path: str) -> bool:
        self._reset_runtime()
        resolved = os.path.abspath(model_path)
        if not os.path.exists(resolved):
            self.last_error = f"模型文件不存在：{resolved}"
            print(f"[Inference] {self.last_error}")
            return False

        suffix = Path(resolved).suffix.lower()
        if suffix not in _SUPPORTED_EXTENSIONS:
            self.last_error = f"不支持的模型格式：{suffix or '无扩展名'}"
            print(f"[Inference] {self.last_error}")
            return False

        try:
            if suffix in {".ts", ".torchscript", ".ptl"}:
                self._load_torchscript(resolved)
            elif suffix in {".pt", ".pth"}:
                self._load_checkpoint(resolved)
            else:
                self._load_onnx(resolved)

            self._validate_loaded_model()
            self.model_loaded = True
            self.model_path = resolved
            print(
                f"[Inference] Loaded {os.path.basename(resolved)} with {self.backend}; "
                f"input_rank={self.input_rank}, sequence={self.sequence_length}, "
                f"features={self.feature_count}"
            )
            return True
        except Exception as exc:
            detail = str(exc).strip().splitlines()[0] if str(exc).strip() else "未知错误"
            if len(detail) > 300:
                detail = detail[:300] + "..."
            self.last_error = f"{type(exc).__name__}: {detail}"
            self.session = None
            self.torch_model = None
            self.backend = None
            self.model_loaded = False
            self.model_path = None
            print(f"[Inference] Failed to load model: {self.last_error}")
            return False

    def _load_torchscript(self, path: str) -> None:
        import torch

        self.torch_model = torch.jit.load(path, map_location="cpu")
        self.torch_model.eval()
        self.backend = "torchscript"
        self.input_rank = 3

    def _load_checkpoint(self, path: str) -> None:
        import torch
        from .cnn_lstm import create_model

        try:
            checkpoint = torch.load(path, map_location="cpu", weights_only=True)
        except TypeError:
            checkpoint = torch.load(path, map_location="cpu")

        if not isinstance(checkpoint, dict) or "state_dict" not in checkpoint:
            raise ValueError(
                ".pt/.pth 文件不是本项目的 checkpoint；请改用 TorchScript .ts 文件"
            )

        self.feature_count = int(checkpoint.get("input_features", DEFAULT_FEATURE_COUNT))
        self.sequence_length = int(
            checkpoint.get("sequence_length", DEFAULT_SEQUENCE_LENGTH)
        )
        num_classes = int(checkpoint.get("num_classes", len(DEFAULT_CLASS_NAMES)))
        class_names = checkpoint.get("class_names")
        if isinstance(class_names, (list, tuple)) and len(class_names) == num_classes:
            self.class_names = [str(name) for name in class_names]
        else:
            self.class_names = [f"Class_{index}" for index in range(num_classes)]

        model = create_model(self.feature_count, num_classes)
        model.load_state_dict(checkpoint["state_dict"], strict=True)
        model.eval()
        self.torch_model = model
        self.backend = "pytorch-checkpoint"
        self.input_rank = 3

    def _load_onnx(self, path: str) -> None:
        import onnxruntime as ort

        self.session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        model_input = self.session.get_inputs()[0]
        shape = list(model_input.shape)
        self.input_rank = len(shape)
        if self.input_rank not in {2, 3}:
            raise ValueError(f"ONNX 输入维度必须为 2 或 3，当前为 {shape}")

        feature_dim = shape[-1]
        if isinstance(feature_dim, int) and feature_dim > 0:
            self.feature_count = feature_dim
        if self.input_rank == 3:
            sequence_dim = shape[-2]
            if isinstance(sequence_dim, int) and sequence_dim > 0:
                self.sequence_length = sequence_dim
        self.backend = "onnx"

    def _validate_loaded_model(self) -> None:
        if self.backend in {"torchscript", "pytorch-checkpoint"}:
            import torch

            candidate_shapes = [
                (1, self.sequence_length, self.feature_count),
                (1, self.feature_count),
            ]
            last_error = None
            for shape in candidate_shapes:
                try:
                    with torch.no_grad():
                        output = self.torch_model(torch.zeros(shape, dtype=torch.float32))
                    if output.ndim != 2:
                        raise ValueError(f"模型输出应为二维 logits，当前为 {tuple(output.shape)}")
                    self.input_rank = len(shape)
                    return
                except Exception as exc:
                    last_error = exc
            raise ValueError(f"模型输入与项目不兼容：{last_error}")

        if self.backend == "onnx":
            input_name = self.session.get_inputs()[0].name
            if self.input_rank == 3:
                sample = np.zeros(
                    (1, self.sequence_length, self.feature_count), dtype=np.float32
                )
            else:
                sample = np.zeros((1, self.feature_count), dtype=np.float32)
            output = self.session.run(None, {input_name: sample})[0]
            if np.asarray(output).ndim != 2:
                raise ValueError(f"ONNX 输出应为二维 logits，当前为 {np.asarray(output).shape}")
            return

        raise RuntimeError("模型后端没有初始化")

    def _prepare_input(self, features: np.ndarray) -> tuple[np.ndarray, bool]:
        x = np.asarray(features, dtype=np.float32)
        single = False

        if x.ndim == 1:
            if x.shape[0] != self.feature_count:
                raise ValueError(
                    f"需要 {self.feature_count} 个特征，实际收到 {x.shape[0]} 个"
                )
            single = True
            if self.input_rank == 3:
                x = np.repeat(
                    x.reshape(1, 1, -1), self.sequence_length, axis=1
                )
            else:
                x = x.reshape(1, -1)

        elif x.ndim == 2:
            if x.shape[1] != self.feature_count:
                raise ValueError(
                    f"需要 {self.feature_count} 个特征，实际收到 {x.shape[1]} 个"
                )
            if self.input_rank == 3:
                if x.shape[0] == self.sequence_length:
                    x = x.reshape(1, self.sequence_length, self.feature_count)
                    single = True
                else:
                    x = np.repeat(x[:, None, :], self.sequence_length, axis=1)
            else:
                # A sequence from the new extractor can still be passed to a
                # legacy one-row ONNX model. Use the latest row as one sample.
                if x.shape[0] == self.sequence_length:
                    x = x[-1:, :]
                    single = True

        elif x.ndim == 3:
            if x.shape[-1] != self.feature_count:
                raise ValueError(
                    f"需要 {self.feature_count} 个特征，实际收到 {x.shape[-1]} 个"
                )
            if self.input_rank == 3:
                if x.shape[1] != self.sequence_length:
                    raise ValueError(
                        f"需要长度为 {self.sequence_length} 的序列，实际为 {x.shape[1]}"
                    )
            else:
                x = x[:, -1, :]
        else:
            raise ValueError(f"不支持的输入形状：{x.shape}")

        return np.ascontiguousarray(x, dtype=np.float32), single

    def predict(self, features: np.ndarray):
        if not self.model_loaded:
            return self._dummy_result()

        x, single = self._prepare_input(features)
        if self.backend in {"torchscript", "pytorch-checkpoint"}:
            import torch

            with torch.no_grad():
                logits = self.torch_model(torch.from_numpy(x)).cpu().numpy()
        elif self.backend == "onnx":
            input_name = self.session.get_inputs()[0].name
            logits = self.session.run(None, {input_name: x})[0]
        else:
            raise RuntimeError("没有可用的推理后端")

        logits = np.asarray(logits, dtype=np.float32)
        if logits.ndim == 1:
            logits = logits.reshape(1, -1)
        probabilities = self._softmax(logits)
        class_ids = np.argmax(probabilities, axis=1)
        confidences = np.max(probabilities, axis=1)
        results = [
            self._build_result(int(class_id), float(confidence), probabilities[index])
            for index, (class_id, confidence) in enumerate(zip(class_ids, confidences))
        ]
        return results[0] if single else results

    def _build_result(
        self, class_id: int, confidence: float, probabilities: Optional[np.ndarray] = None
    ) -> dict:
        class_name = (
            self.class_names[class_id]
            if 0 <= class_id < len(self.class_names)
            else f"Class_{class_id}"
        )
        result = {
            "class_id": class_id,
            "class_name": class_name,
            "confidence": round(confidence, 4),
            "risk_level": self._determine_risk(class_id, confidence),
            "is_attack": class_id != 0,
        }
        if probabilities is not None:
            result["probabilities"] = {
                (
                    self.class_names[index]
                    if index < len(self.class_names)
                    else f"Class_{index}"
                ): round(float(value), 6)
                for index, value in enumerate(probabilities)
            }
        return result

    @staticmethod
    def _determine_risk(class_id: int, confidence: float) -> str:
        if class_id == 0:
            return "low"
        if confidence >= RISK_THRESHOLDS["critical"]:
            return "critical"
        if confidence >= RISK_THRESHOLDS["high"]:
            return "high"
        if confidence >= RISK_THRESHOLDS["medium"]:
            return "medium"
        return "low"

    @staticmethod
    def _softmax(logits: np.ndarray) -> np.ndarray:
        exponent = np.exp(logits - np.max(logits, axis=1, keepdims=True))
        denominator = np.sum(exponent, axis=1, keepdims=True)
        return exponent / np.maximum(denominator, 1e-12)

    def _dummy_result(self) -> dict:
        return {
            "class_id": 0,
            "class_name": self.class_names[0] if self.class_names else "Normal",
            "confidence": 0.0,
            "risk_level": "low",
            "is_attack": False,
            "error": self.last_error or "模型未加载",
        }


_engine: Optional[InferenceEngine] = None


def _read_model_config() -> dict:
    try:
        return json.loads(_MODEL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_model_config(model_path: str) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _MODEL_CONFIG_PATH.write_text(
        json.dumps(
            {"active_model_path": os.path.abspath(model_path)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def get_active_model_path() -> Optional[str]:
    configured = _read_model_config().get("active_model_path")
    if configured and os.path.exists(configured):
        return os.path.abspath(configured)
    for default_path in (_DEFAULT_TS_PATH, _DEFAULT_PT_PATH, _DEFAULT_ONNX_PATH):
        if default_path.exists():
            return str(default_path.resolve())
    return None


def _model_info(model_path: str) -> Dict[str, object]:
    path = Path(model_path).resolve()
    stat = path.stat()
    return {
        "id": path.name,
        "name": path.stem,
        "filename": path.name,
        "path": str(path),
        "format": path.suffix.lstrip(".").lower(),
        "size_bytes": stat.st_size,
        "updated_at": stat.st_mtime,
        "active": get_active_model_path() == str(path),
    }


def list_models() -> List[Dict[str, object]]:
    paths: List[Path] = []
    # Show one built-in model to avoid three entries with the same name.
    for default_path in (_DEFAULT_TS_PATH, _DEFAULT_PT_PATH, _DEFAULT_ONNX_PATH):
        if default_path.exists():
            paths.append(default_path)
            break
    if _USER_MODEL_DIR.is_dir():
        for path in sorted(_USER_MODEL_DIR.iterdir()):
            if path.suffix.lower() in _SUPPORTED_EXTENSIONS:
                paths.append(path)

    seen = set()
    output = []
    for path in paths:
        resolved = str(path.resolve())
        if resolved not in seen:
            seen.add(resolved)
            output.append(_model_info(resolved))
    return output


def resolve_model_path(model_id: str) -> Optional[str]:
    base = os.path.basename(model_id or "")
    for model in list_models():
        if model["id"] == base or model["filename"] == base:
            return str(model["path"])
    return None


def switch_model(model_path: str) -> bool:
    engine = get_engine()
    if not engine.load(model_path):
        return False
    _write_model_config(model_path)
    return True


def is_active_model_loaded() -> bool:
    active = get_active_model_path()
    return bool(
        _engine
        and _engine.model_loaded
        and active
        and _engine.model_path == os.path.abspath(active)
    )


def get_engine(model_path: Optional[str] = None) -> InferenceEngine:
    global _engine
    target = model_path or get_active_model_path()
    if _engine is None:
        _engine = InferenceEngine(target)
    elif target and os.path.abspath(target) != _engine.model_path:
        _engine.load(target)
    return _engine
