"""Runs the classifier against evaluation/dataset.jsonl as part of the normal
test suite. Local classification costs nothing, so — unlike the earlier
Anthropic-backed pipeline — this can be a real, always-on regression gate:
a weight/threshold change in app/classifier/patterns.py that regresses
accuracy fails this test immediately.
"""

import json
from pathlib import Path

from app.classifier.extractor import RuleBasedExtractor

DATASET_PATH = Path(__file__).parent.parent / "evaluation" / "dataset.jsonl"

# Calibrated in Task 8 against this exact dataset; see that task's commit
# message for the observed numbers this bar was set from.
MIN_CLASSIFICATION_ACCURACY = 0.8


def _load_dataset() -> list[dict]:
    with DATASET_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def test_classification_accuracy_meets_minimum_bar() -> None:
    extractor = RuleBasedExtractor()
    examples = _load_dataset()

    correct = 0
    for example in examples:
        result = extractor.classify_and_extract(
            subject=example["subject"],
            sender=example["sender"],
            date=example["date"],
            body=example["body"],
        )
        if result.is_job_related == example["expected"]["is_job_related"]:
            correct += 1

    accuracy = correct / len(examples)
    assert accuracy >= MIN_CLASSIFICATION_ACCURACY, (
        f"classification accuracy {accuracy:.2f} fell below the {MIN_CLASSIFICATION_ACCURACY} bar "
        f"({correct}/{len(examples)} correct)"
    )
