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

# Calibrated 2026-09-15 against this exact 17-example dataset: after Task 8's
# pattern fixes (company-token capitalization guard and the "you the X
# position at Y" offer phrasing in app/classifier/fields.py), RuleBasedExtractor
# scored 17/17 (1.00) classification accuracy, 11/11 (1.00) status accuracy,
# 10/10 (1.00) position accuracy, and 10/11 (0.91) company accuracy (the one
# company miss — an assessment-platform email naming the hiring company only
# as a sentence subject in the body, with the sender domain being the ATS
# platform, not the company, and no display name on the sender address — is an
# accepted, documented extraction limitation, not a classification failure).
# Set with a small margin below the observed 1.00 classification accuracy so
# the bar doesn't flap on a single new hard example in a future dataset
# addition, not because 0.95 is independently meaningful.
MIN_CLASSIFICATION_ACCURACY = 0.95


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
