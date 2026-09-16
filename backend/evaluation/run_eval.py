# backend/evaluation/run_eval.py
"""Run the classification/extraction pipeline against a labeled dataset.

Local classification has no external dependency and costs nothing to run —
unlike the earlier Anthropic-backed version, this is safe to run as often as
you like. Run manually:

    cd backend
    uv run python -m evaluation.run_eval

The same dataset (evaluation/dataset.jsonl) also backs the always-on
regression check in tests/test_evaluation_accuracy.py.
"""

import json
from pathlib import Path

from app.classifier.extractor import RuleBasedExtractor

DATASET_PATH = Path(__file__).parent / "dataset.jsonl"


def _load_dataset() -> list[dict]:
    with DATASET_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> None:
    extractor = RuleBasedExtractor()
    examples = _load_dataset()

    classification_correct = 0
    field_matches = {"company": 0, "position": 0, "status": 0}
    field_totals = {"company": 0, "position": 0, "status": 0}

    for example in examples:
        result = extractor.classify_and_extract(
            subject=example["subject"],
            sender=example["sender"],
            date=example["date"],
            body=example["body"],
        )
        expected = example["expected"]

        if result.is_job_related == expected["is_job_related"]:
            classification_correct += 1

        if expected["is_job_related"]:
            for field in ("company", "position", "status"):
                if field in expected:
                    field_totals[field] += 1
                    if getattr(result, field) == expected[field]:
                        field_matches[field] += 1

    print(f"Classification accuracy: {classification_correct}/{len(examples)}")
    for field, total in field_totals.items():
        if total:
            print(f"{field} accuracy: {field_matches[field]}/{total}")


if __name__ == "__main__":
    main()
