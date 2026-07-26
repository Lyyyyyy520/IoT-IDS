"""Verify the installed CNN+LSTM model bundle without starting the web UI."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from models.inference import InferenceEngine, get_active_model_path
from services.feature_extract import FeatureExtractor


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the IoT IDS model bundle")
    parser.add_argument("--csv", help="Optional N-BaIoT CSV for one real prediction test")
    args = parser.parse_args()

    required = [
        BACKEND_DIR / "data" / "best_model.ts",
        BACKEND_DIR / "data" / "feature_schema.json",
        BACKEND_DIR / "data" / "scaler.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print("[失败] 缺少模型文件：")
        for path in missing:
            print(f"  - {path}")
        return 2

    model_path = get_active_model_path()
    if not model_path:
        print("[失败] 未找到可用模型")
        return 3

    extractor = FeatureExtractor()
    try:
        extractor.validate_bundle()
    except Exception as exc:
        print(f"[失败] 特征/标准化文件无效：{exc}")
        return 4

    engine = InferenceEngine(model_path)
    if not engine.model_loaded:
        print(f"[失败] 模型加载失败：{engine.last_error}")
        return 5

    sample = np.zeros((engine.sequence_length, engine.feature_count), dtype=np.float32)
    try:
        result = engine.predict(sample)
    except Exception as exc:
        print(f"[失败] 模型推理失败：{type(exc).__name__}: {exc}")
        return 6

    output = {
        "status": "ok",
        "model": str(Path(model_path).resolve()),
        "backend": engine.backend,
        "input_shape": [1, engine.sequence_length, engine.feature_count]
        if engine.input_rank == 3
        else [1, engine.feature_count],
        "classes": engine.class_names,
        "scaler": str(extractor.scaler_path) if extractor.scaler_path else None,
        "smoke_prediction": result,
    }

    if args.csv:
        try:
            sequences = extractor.extract_from_csv(args.csv, max_sequences=3)
            output["csv_sequences"] = len(sequences)
            output["csv_predictions"] = engine.predict(sequences) if sequences else []
        except Exception as exc:
            print(f"[失败] CSV 验证失败：{type(exc).__name__}: {exc}")
            return 7

    print(json.dumps(output, ensure_ascii=False, indent=2))
    print("\n[通过] CNN+LSTM 模型、21维特征配置和标准化器均可正常使用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
