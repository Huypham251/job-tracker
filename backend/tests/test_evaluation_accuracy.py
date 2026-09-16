"""Runs the classifier against evaluation/dataset.jsonl as part of the normal
test suite. Local classification costs nothing, so — unlike the earlier
Anthropic-backed pipeline — this can be a real, always-on regression gate:
a weight/threshold change in app/classifier/patterns.py that regresses
accuracy fails this test immediately.

Mirrors the same per-field accuracy computation evaluation/run_eval.py does,
so this covers everything Task 8's calibration pass actually tuned (not just
is_job_related): status, company, and position accuracy each get their own
gate, not only overall classification.
"""

import json
from pathlib import Path

from app.classifier.extractor import RuleBasedExtractor

DATASET_PATH = Path(__file__).parent.parent / "evaluation" / "dataset.jsonl"

# Calibrated 2026-09-16 against this exact 18-example dataset (after fixing
# final-review findings #1 and #3, and adding one new dataset example for
# #3's "no-auxiliary received your application" phrasing), by running
# `uv run python -m evaluation.run_eval` and reading its printed numbers
# directly — not guessed:
#   Classification accuracy: 18/18 (1.00)
#   status accuracy:         12/12 (1.00)
#   position accuracy:       11/11 (1.00)
#   company accuracy:        11/12 (0.92) — the one miss is the same accepted,
#     documented extraction limitation from Task 8's calibration commit (an
#     assessment-platform email naming the hiring company only as a sentence
#     subject in the body, sender domain is the ATS/assessment platform, no
#     display name on the sender address).
#
# Every bar below is set to genuinely tolerate one additional miss beyond
# today's dataset (not just barely clear today's number) — with a dataset
# this small (11-18 examples per field), a bar set only a hair below 100%
# would fail the very next time a single new hard example is added, which
# is not a meaningful safety margin, just a bar that hasn't been tested yet.
# These are honestly-computed: each is chosen so one more wrong example
# still passes, and two more wrong examples fails.
MIN_CLASSIFICATION_ACCURACY = 0.90  # 17/18 passes (0.94); 16/18 fails (0.89)
MIN_STATUS_ACCURACY = 0.90  # 11/12 passes (0.92); 10/12 fails (0.83)
MIN_POSITION_ACCURACY = 0.90  # 10/11 passes (0.91); 9/11 fails (0.82)
MIN_COMPANY_ACCURACY = 0.80  # 10/12 passes (0.83, tolerating the existing miss
# plus one more); 9/12 fails (0.75)


def _load_dataset() -> list[dict]:
    with DATASET_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def test_classification_accuracy_meets_minimum_bar() -> None:
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

    classification_accuracy = classification_correct / len(examples)
    assert classification_accuracy >= MIN_CLASSIFICATION_ACCURACY, (
        f"classification accuracy {classification_accuracy:.2f} fell below the "
        f"{MIN_CLASSIFICATION_ACCURACY} bar ({classification_correct}/{len(examples)} correct)"
    )

    for field, min_accuracy in (
        ("status", MIN_STATUS_ACCURACY),
        ("company", MIN_COMPANY_ACCURACY),
        ("position", MIN_POSITION_ACCURACY),
    ):
        total = field_totals[field]
        correct = field_matches[field]
        accuracy = correct / total
        assert accuracy >= min_accuracy, (
            f"{field} accuracy {accuracy:.2f} fell below the {min_accuracy} bar "
            f"({correct}/{total} correct)"
        )
