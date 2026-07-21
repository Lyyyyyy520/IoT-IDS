"""
ONNX Runtime Inference Engine

Loads a trained CNN+LSTM model exported as ONNX and runs inference.

Usage:
    engine = InferenceEngine('data/best_model.onnx')
    result = engine.predict(feature_vector)  # 21-dim numpy array
    # result = {'class': 'Mirai', 'confidence': 0.97, 'risk_level': 'critical'}
"""
import os
import json
import numpy as np
from typing import Optional, List, Dict

CLASS_NAMES = ['Normal', 'Mirai', 'Gafgyt', 'Hajime', 'Other']

RISK_THRESHOLDS = {
    'critical': 0.95,  # >= 0.95 AND attack class
    'high': 0.85,      # >= 0.85
    'medium': 0.70,    # >= 0.70
    # below 0.70 → low (normal traffic)
}


class InferenceEngine:
    """Wrapper around ONNX Runtime for IoT IDS model inference."""

    def __init__(self, model_path: Optional[str] = None):
        self.session = None
        self.model_loaded = False
        self.model_path: Optional[str] = None

        if model_path:
            self.load(model_path)

    def load(self, model_path: str) -> bool:
        """Load ONNX model from file. Returns True on success."""
        resolved_path = os.path.abspath(model_path)
        if not os.path.exists(resolved_path):
            print(f'[Inference] Model not found: {resolved_path}')
            return False

        try:
            import onnxruntime as ort
            session = ort.InferenceSession(
                resolved_path,
                providers=['CPUExecutionProvider'],
            )
            self.session = session
            self.model_loaded = True
            self.model_path = resolved_path
            # Print model info
            input_name = self.session.get_inputs()[0].name
            input_shape = self.session.get_inputs()[0].shape
            output_name = self.session.get_outputs()[0].name
            print(f'[Inference] Model loaded: {os.path.basename(resolved_path)}')
            print(f'  Input : {input_name} {input_shape}')
            print(f'  Output: {output_name}')
            return True
        except ImportError:
            print('[Inference] onnxruntime not installed')
            return False
        except Exception as e:
            print(f'[Inference] Failed to load model: {e}')
            return False

    def predict(self, features: np.ndarray) -> dict:
        """
        Run inference on a single feature vector (or batch).

        Args:
            features: numpy array of shape (21,) or (N, 21)

        Returns:
            dict with class_name, class_id, confidence, risk_level, probabilities
        """
        if not self.model_loaded:
            return self._dummy_result(features)

        # Ensure float32 and correct shape
        x = np.asarray(features, dtype=np.float32)
        single = (x.ndim == 1)
        if single:
            x = x.reshape(1, -1)

        # Run ONNX inference
        input_name = self.session.get_inputs()[0].name
        logits = self.session.run(None, {input_name: x})[0]  # (N, 5)

        # Softmax
        probs = self._softmax(logits)
        class_ids = np.argmax(probs, axis=1)
        confidences = np.max(probs, axis=1)

        if single:
            class_id = int(class_ids[0])
            conf = float(confidences[0])
            return self._build_result(class_id, conf)

        return [
            self._build_result(int(cid), float(cf))
            for cid, cf in zip(class_ids, confidences)
        ]

    def _build_result(self, class_id: int, confidence: float) -> dict:
        class_name = CLASS_NAMES[class_id] if class_id < len(CLASS_NAMES) else 'Unknown'
        risk_level = self._determine_risk(class_id, confidence)
        return {
            'class_id': class_id,
            'class_name': class_name,
            'confidence': round(confidence, 4),
            'risk_level': risk_level,
            'is_attack': class_id != 0,
        }

    @staticmethod
    def _determine_risk(class_id: int, confidence: float) -> str:
        if class_id == 0:  # Normal
            return 'low'
        # Attack classes: use confidence thresholds
        if confidence >= RISK_THRESHOLDS['critical']:
            return 'critical'
        if confidence >= RISK_THRESHOLDS['high']:
            return 'high'
        if confidence >= RISK_THRESHOLDS['medium']:
            return 'medium'
        return 'low'

    @staticmethod
    def _softmax(logits: np.ndarray) -> np.ndarray:
        exp = np.exp(logits - np.max(logits, axis=1, keepdims=True))
        return exp / np.sum(exp, axis=1, keepdims=True)

    def _dummy_result(self, features: np.ndarray) -> dict:
        """Fallback when model isn't loaded — returns simulated results."""
        import hashlib
        x = np.asarray(features).flatten()
        # Deterministic "random" from feature hash for demo
        h = int(hashlib.md5(x.tobytes()).hexdigest()[:8], 16) % 100
        if h < 30:
            return self._build_result(0, 0.4 + (h / 100))
        elif h < 55:
            return self._build_result(1, 0.85 + (h % 15) / 100)
        elif h < 75:
            return self._build_result(2, 0.80 + (h % 20) / 100)
        elif h < 88:
            return self._build_result(3, 0.70 + (h % 15) / 100)
        else:
            return self._build_result(4, 0.60 + (h % 25) / 100)


# ---- Global singleton ----
_engine: Optional[InferenceEngine] = None

_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))
_USER_MODEL_DIR = os.path.join(_DATA_DIR, 'models')
_DEFAULT_MODEL_PATH = os.path.join(_DATA_DIR, 'best_model.onnx')
_MODEL_CONFIG_PATH = os.path.join(_DATA_DIR, 'model_config.json')


def _read_model_config() -> dict:
    if not os.path.exists(_MODEL_CONFIG_PATH):
        return {}
    try:
        with open(_MODEL_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _write_model_config(model_path: str) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_MODEL_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump({'active_model_path': os.path.abspath(model_path)}, f, ensure_ascii=False, indent=2)


def get_active_model_path() -> Optional[str]:
    config_path = _read_model_config().get('active_model_path')
    if config_path and os.path.exists(config_path):
        return os.path.abspath(config_path)
    if os.path.exists(_DEFAULT_MODEL_PATH):
        return os.path.abspath(_DEFAULT_MODEL_PATH)
    return None


def _model_info(model_path: str) -> Dict[str, object]:
    model_path = os.path.abspath(model_path)
    stat = os.stat(model_path)
    active_path = get_active_model_path()
    return {
        'id': os.path.basename(model_path),
        'name': os.path.splitext(os.path.basename(model_path))[0],
        'filename': os.path.basename(model_path),
        'path': model_path,
        'size_bytes': stat.st_size,
        'updated_at': stat.st_mtime,
        'active': active_path == model_path,
    }


def list_models() -> List[Dict[str, object]]:
    """List built-in and user-uploaded ONNX models."""
    model_paths = []
    if os.path.exists(_DEFAULT_MODEL_PATH):
        model_paths.append(os.path.abspath(_DEFAULT_MODEL_PATH))
    if os.path.isdir(_USER_MODEL_DIR):
        for filename in sorted(os.listdir(_USER_MODEL_DIR)):
            if filename.lower().endswith('.onnx'):
                model_paths.append(os.path.abspath(os.path.join(_USER_MODEL_DIR, filename)))

    seen = set()
    models = []
    for path in model_paths:
        if path in seen:
            continue
        seen.add(path)
        models.append(_model_info(path))
    return models


def resolve_model_path(model_id: str) -> Optional[str]:
    """Resolve a model filename/id to a known ONNX model path."""
    if not model_id:
        return None
    model_id = os.path.basename(model_id)
    for model in list_models():
        if model['id'] == model_id or model['filename'] == model_id:
            return str(model['path'])
    return None


def switch_model(model_path: str) -> bool:
    """Load and persist a new active model. Keeps current model if loading fails."""
    global _engine
    engine = get_engine()
    if not engine.load(model_path):
        return False
    _write_model_config(model_path)
    return True


def is_active_model_loaded() -> bool:
    """Return whether the active model is already loaded without initializing ONNX Runtime."""
    active_path = get_active_model_path()
    return bool(
        _engine
        and _engine.model_loaded
        and active_path
        and _engine.model_path == os.path.abspath(active_path)
    )


def get_engine(model_path: Optional[str] = None) -> InferenceEngine:
    """Get or create the global inference engine."""
    global _engine
    target_path = model_path or get_active_model_path()
    if _engine is None:
        _engine = InferenceEngine(target_path)
    elif target_path and os.path.abspath(target_path) != _engine.model_path:
        _engine.load(target_path)
    return _engine
