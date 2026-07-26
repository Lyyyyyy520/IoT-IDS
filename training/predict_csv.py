"""Run the trained model on an N-BaIoT CSV file."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from models.inference import InferenceEngine, get_active_model_path
from services.feature_extract import FeatureExtractor


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", help="N-BaIoT CSV containing the original 115 columns")
    parser.add_argument("--max_sequences", type=int, default=2000)
    args = parser.parse_args()

    model_path = get_active_model_path()
    if not model_path:
        raise SystemExit("No trained model was found under backend/data")

    extractor = FeatureExtractor()
    sequences = extractor.extract_from_csv(args.csv, max_sequences=args.max_sequences)
    if not sequences:
        raise SystemExit("The CSV does not contain enough rows for one sequence")

    engine = InferenceEngine(model_path)
    results = engine.predict(sequences)
    counts = Counter(result["class_name"] for result in results)
    attacks = sum(result["is_attack"] for result in results)
    output = {
        "model": model_path,
        "csv": str(Path(args.csv).resolve()),
        "sequence_count": len(results),
        "attack_count": attacks,
        "class_counts": dict(counts),
        "first_results": results[:10],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
