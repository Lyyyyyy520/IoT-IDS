"""
ONNX Runtime Inference Engine

Loads a trained CNN+LSTM model exported as ONNX and runs inference.

Usage:
    engine = InferenceEngine('data/best_model.onnx')
    result = engine.predict(feature_vector)  # 21-dim numpy array
    # result = {'class': 'Mirai', 'confidence': 0.97, 'risk_level': 'critical'}
"""
import os
import numpy as np
from typing import Optional

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

        if model_path:
            self.load(model_path)

    def load(self, model_path: str) -> bool:
        """Load ONNX model from file. Returns True on success."""
        if not os.path.exists(model_path):
            print(f'[Inference] Model not found: {model_path}')
            return False

        try:
            import onnxruntime as ort
            self.session = ort.InferenceSession(
                model_path,
                providers=['CPUExecutionProvider'],
            )
            self.model_loaded = True
            # Print model info
            input_name = self.session.get_inputs()[0].name
            input_shape = self.session.get_inputs()[0].shape
            output_name = self.session.get_outputs()[0].name
            print(f'[Inference] Model loaded: {os.path.basename(model_path)}')
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


def get_engine(model_path: Optional[str] = None) -> InferenceEngine:
    """Get or create the global inference engine."""
    global _engine
    if _engine is None:
        _engine = InferenceEngine(model_path)
    return _engine
