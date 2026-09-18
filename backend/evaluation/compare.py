# backend/evaluation/compare.py
"""Diff the current RuleBasedExtractor's metrics against the recorded Phase 6 baseline
(evaluation/baseline_metrics.json, written once by Task 4 before any classifier code
changed). Run after every checkpoint:

    cd backend
    uv run python -m evaluation.compare
"""

import json
from pathlib import Path
from typing import Any

from app.classifier.extractor import RuleBasedExtractor
from evaluation.run_eval import evaluate, load_dataset

BASELINE_PATH = Path(__file__).parent / "baseline_metrics.json"

_TRACKED_METRICS: list[tuple[str, ...]] = [
    ("is_job_related", "precision"),
    ("is_job_related", "recall"),
    ("is_job_related", "f1"),
    ("company_exact_accuracy",),
    ("company_fuzzy_accuracy",),
    ("position_exact_accuracy",),
    ("position_fuzzy_accuracy",),
    ("precision_at_threshold",),
    ("auto_apply_rate",),
    ("review_rate",),
]


def _get(section: dict, path: tuple[str, ...]) -> float:
    value: Any = section
    for key in path:
        value = value[key]
    return value


def main() -> None:
    if not BASELINE_PATH.exists():
        raise SystemExit(
            f"{BASELINE_PATH} does not exist yet. Task 4 records it once, before any "
            "classifier code changes, by running today's extractor and saving its "
            "evaluate() output there. Nothing to compare against."
        )
    baseline = json.loads(BASELINE_PATH.read_text())
    current = evaluate(RuleBasedExtractor(), load_dataset())

    print(f"{'metric':<28} {'baseline':>10} {'current':>10} {'delta':>10}")
    for path in _TRACKED_METRICS:
        label = ".".join(path)
        b, c = _get(baseline["overall"], path), _get(current["overall"], path)
        delta = c - b
        marker = "  REGRESSION" if delta < -0.001 else ""
        print(f"{label:<28} {b:>10.3f} {c:>10.3f} {delta:>+10.3f}{marker}")

    print()
    header = f"{'category':<20} {'is_job_related_f1':>20} {'auto_apply_rate':>18} {'precision_at_threshold':>24}"
    print(header)
    for category in sorted(current["by_category"]):
        cur = current["by_category"][category]
        base = baseline["by_category"].get(category, {})
        base_f1 = base.get("is_job_related", {}).get("f1", "n/a")
        base_auto = base.get("auto_apply_rate", "n/a")
        base_prec = base.get("precision_at_threshold", "n/a")
        print(
            f"{category:<20} "
            f"{cur['is_job_related']['f1']:>10.3f} (was {base_f1})  "
            f"{cur['auto_apply_rate']:>8.3f} (was {base_auto})  "
            f"{cur['precision_at_threshold']:>10.3f} (was {base_prec})"
        )


if __name__ == "__main__":
    main()
