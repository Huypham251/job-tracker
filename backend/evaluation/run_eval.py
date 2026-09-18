# backend/evaluation/run_eval.py
"""Run the classification/extraction pipeline against a labeled dataset and report the
full Phase 6 metrics set (precision/recall/F1, per-status breakdown, exact and fuzzy
extraction accuracy, precision-at-threshold, auto-apply rate, review rate), both
overall and per dataset `category`.

Local classification has no external dependency and costs nothing to run — safe to run
as often as you like. Run manually:

    cd backend
    uv run python -m evaluation.run_eval

evaluate() is also imported directly by evaluation/compare.py (checkpoint diffing) and
by tests/test_evaluation_run_eval.py.
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from app.classifier.extractor import RuleBasedExtractor

DATASET_PATH = Path(__file__).parent / "dataset.jsonl"

# Mirrors settings.classification_confidence_threshold (app/core/config.py). Duplicated
# rather than imported: Settings() requires .env-backed fields (database_url, Google
# OAuth credentials, the Gmail token encryption key, ...) that this standalone,
# zero-setup evaluation script must not depend on just to compute a metrics report.
# tests/test_evaluation_accuracy.py::test_confidence_threshold_constant_matches_settings
# keeps the two from silently drifting apart.
CONFIDENCE_THRESHOLD = 0.85

STATUSES = ["applied", "oa", "interview", "rejected", "offer", "other"]

# rapidfuzz token_sort_ratio threshold for "close enough to call the same extraction"
# (trailing punctuation, minor casing/spacing differences) — a couple points stricter
# than pipeline/matching.py's STRONG_MATCH_THRESHOLD=85, since that threshold answers a
# different question ("is this likely the same application") than this one ("did the
# classifier basically get the right string").
FUZZY_MATCH_THRESHOLD = 90


def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def _fuzzy_match(actual: str | None, expected: str | None) -> bool:
    if actual is None or expected is None:
        return actual == expected
    return fuzz.token_sort_ratio(actual, expected) >= FUZZY_MATCH_THRESHOLD


def _new_bucket() -> dict:
    return {
        "n": 0,
        "tp": 0, "fp": 0, "fn": 0, "tn": 0,
        "status_tp": defaultdict(int), "status_fp": defaultdict(int), "status_fn": defaultdict(int),
        "status_total": 0, "status_correct": 0,
        "company_total": 0, "company_exact": 0, "company_fuzzy": 0,
        "position_total": 0, "position_exact": 0, "position_fuzzy": 0,
        "cleared_threshold": 0,
        "cleared_threshold_correct": 0,
        "true_positive_total": 0,
        "true_positive_cleared": 0,
        "review_candidates": 0,
    }


def _score_one(bucket: dict, result, expected: dict) -> None:
    bucket["n"] += 1
    predicted_related = result.is_job_related
    expected_related = expected["is_job_related"]

    if predicted_related and expected_related:
        bucket["tp"] += 1
    elif predicted_related and not expected_related:
        bucket["fp"] += 1
    elif not predicted_related and expected_related:
        bucket["fn"] += 1
    else:
        bucket["tn"] += 1

    fully_correct = predicted_related == expected_related

    if expected_related:
        bucket["true_positive_total"] += 1

        expected_status = expected.get("status")
        if expected_status:
            # Scoped to true positives only (expected_related=True): a false-positive
            # item's status guess is deliberately not counted here, since the metric
            # this feeds ("does the classifier confuse rejected vs. interview") is
            # about confusion among genuinely job-related mail, not spam.
            bucket["status_total"] += 1
            status_correct = result.status == expected_status
            bucket["status_correct"] += int(status_correct)
            if status_correct:
                bucket["status_tp"][expected_status] += 1
            else:
                bucket["status_fn"][expected_status] += 1
                if result.status:
                    bucket["status_fp"][result.status] += 1
            fully_correct = fully_correct and status_correct

        if "company" in expected:
            bucket["company_total"] += 1
            expected_company = expected["company"]
            exact = result.company == expected_company
            bucket["company_exact"] += int(exact)
            bucket["company_fuzzy"] += int(exact or _fuzzy_match(result.company, expected_company))
            fully_correct = fully_correct and exact

        if "position" in expected:
            bucket["position_total"] += 1
            expected_position = expected["position"]
            exact = result.position == expected_position
            bucket["position_exact"] += int(exact)
            bucket["position_fuzzy"] += int(exact or _fuzzy_match(result.position, expected_position))
            fully_correct = fully_correct and exact

    cleared = predicted_related and result.confidence >= CONFIDENCE_THRESHOLD
    if cleared:
        bucket["cleared_threshold"] += 1
        bucket["cleared_threshold_correct"] += int(fully_correct)
    elif predicted_related:
        bucket["review_candidates"] += 1

    if expected_related and cleared:
        bucket["true_positive_cleared"] += 1


def _safe_div(n: int, d: int) -> float:
    return n / d if d else 0.0


def _finalize(bucket: dict) -> dict:
    tp, fp, fn, tn = bucket["tp"], bucket["fp"], bucket["fn"], bucket["tn"]
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * precision * recall, precision + recall) if (precision + recall) else 0.0

    per_status = {}
    for status in STATUSES:
        s_tp, s_fp, s_fn = bucket["status_tp"][status], bucket["status_fp"][status], bucket["status_fn"][status]
        if not (s_tp or s_fp or s_fn):
            continue
        s_precision = _safe_div(s_tp, s_tp + s_fp)
        s_recall = _safe_div(s_tp, s_tp + s_fn)
        s_f1 = _safe_div(2 * s_precision * s_recall, s_precision + s_recall) if (s_precision + s_recall) else 0.0
        per_status[status] = {"precision": round(s_precision, 3), "recall": round(s_recall, 3), "f1": round(s_f1, 3)}

    return {
        "n": bucket["n"],
        "classification_accuracy": round(_safe_div(tp + tn, bucket["n"]), 3),
        "is_job_related": {
            "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        },
        "per_status": per_status,
        "status_accuracy": round(_safe_div(bucket["status_correct"], bucket["status_total"]), 3),
        "company_exact_accuracy": round(_safe_div(bucket["company_exact"], bucket["company_total"]), 3),
        "company_fuzzy_accuracy": round(_safe_div(bucket["company_fuzzy"], bucket["company_total"]), 3),
        "position_exact_accuracy": round(_safe_div(bucket["position_exact"], bucket["position_total"]), 3),
        "position_fuzzy_accuracy": round(_safe_div(bucket["position_fuzzy"], bucket["position_total"]), 3),
        "precision_at_threshold": round(_safe_div(bucket["cleared_threshold_correct"], bucket["cleared_threshold"]), 3),
        "auto_apply_rate": round(_safe_div(bucket["true_positive_cleared"], bucket["true_positive_total"]), 3),
        "review_rate": round(_safe_div(bucket["review_candidates"], bucket["true_positive_total"]), 3),
    }


def evaluate(extractor, examples: list[dict]) -> dict[str, Any]:
    """Run `extractor` over `examples` and compute the full metrics set, both overall
    and broken out per dataset `category`. Returns a plain, JSON-serializable dict."""
    overall = _new_bucket()
    by_category: dict[str, dict] = defaultdict(_new_bucket)

    for example in examples:
        result = extractor.classify_and_extract(
            subject=example["subject"],
            sender=example["sender"],
            date=example["date"],
            body=example["body"],
        )
        category = example.get("category", "uncategorized")
        for bucket in (overall, by_category[category]):
            _score_one(bucket, result, example["expected"])

    return {
        "overall": _finalize(overall),
        "by_category": {cat: _finalize(b) for cat, b in by_category.items()},
    }


def _print_report(report: dict) -> None:
    overall = report["overall"]
    rel = overall["is_job_related"]
    print(f"n={overall['n']}")
    print(f"is_job_related: precision={rel['precision']} recall={rel['recall']} f1={rel['f1']} "
          f"(tp={rel['tp']} fp={rel['fp']} fn={rel['fn']} tn={rel['tn']})")
    for status, m in overall["per_status"].items():
        print(f"  status={status}: precision={m['precision']} recall={m['recall']} f1={m['f1']}")
    print(f"company: exact={overall['company_exact_accuracy']} fuzzy={overall['company_fuzzy_accuracy']}")
    print(f"position: exact={overall['position_exact_accuracy']} fuzzy={overall['position_fuzzy_accuracy']}")
    print(f"precision_at_threshold={overall['precision_at_threshold']} "
          f"auto_apply_rate={overall['auto_apply_rate']} review_rate={overall['review_rate']}")
    print()
    print("By category:")
    for category, m in sorted(report["by_category"].items()):
        print(
            f"  {category} (n={m['n']}): is_job_related_f1={m['is_job_related']['f1']} "
            f"company_fuzzy={m['company_fuzzy_accuracy']} position_fuzzy={m['position_fuzzy_accuracy']} "
            f"precision_at_threshold={m['precision_at_threshold']} auto_apply_rate={m['auto_apply_rate']}"
        )


def main() -> None:
    report = evaluate(RuleBasedExtractor(), load_dataset())
    _print_report(report)


if __name__ == "__main__":
    main()
