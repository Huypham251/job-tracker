# backend/evaluation/run_eval.py
"""Run the classifier against a labeled dataset and report metrics: precision/recall/F1,
per-status breakdown, exact/fuzzy/normalized extraction accuracy, junk-company rate,
precision-at-threshold, auto-apply rate, calibration — overall and per `category`.

Two datasets:
- evaluation/dataset.jsonl (committed): synthetic examples plus, from Phase 11,
  pseudonymized real-derived `real_*` examples;
- the private real-mail set (Phase 11, never in the repo): `--real`, dev split by
  default. The held-out test split is scored once, at Phase 11's end, with
  `--split test --final`.

    cd backend
    uv run python -m evaluation.run_eval
    uv run python -m evaluation.run_eval --real [--misses] [--json PATH]

Local classification costs nothing — run as often as you like. evaluate() is also used
by evaluation/compare.py, evaluation/bars.py and the tests.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from app.classifier.extractor import RuleBasedExtractor
from app.gmail.google_api import clean_body
from app.pipeline.matching import normalize as normalize_company

DATASET_PATH = Path(__file__).parent / "dataset.jsonl"

# Mirrors settings.classification_confidence_threshold (app/core/config.py). Duplicated
# rather than imported: Settings() requires .env-backed fields this standalone script
# must not depend on. tests/test_evaluation_accuracy.py keeps the two in sync.
CONFIDENCE_THRESHOLD = 0.85

STATUSES = ["applied", "oa", "interview", "rejected", "offer", "other"]

# rapidfuzz token_sort_ratio threshold for "close enough to call the same extraction"
# (trailing punctuation, minor casing/spacing differences).
FUZZY_MATCH_THRESHOLD = 90

REAL_CATEGORY_PREFIX = "real_"


def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def is_real_derived(example: dict) -> bool:
    return example.get("category", "").startswith(REAL_CATEGORY_PREFIX)


def synthetic_examples(examples: list[dict]) -> list[dict]:
    """The hand-written examples the Phase 4b–7 bars were calibrated on, kept apart from
    real-derived ones so adding real examples never moves those bars."""
    return [e for e in examples if not is_real_derived(e)]


def real_derived_examples(examples: list[dict]) -> list[dict]:
    return [e for e in examples if is_real_derived(e)]


def example_body(example: dict) -> str:
    """Private real examples carry the raw decoded MIME part, cleaned here by the same
    function the sync worker uses, so ingestion changes are measured too (Phase 11
    spec §3.1). Committed examples carry an already-clean `body`."""
    if "raw_body" in example:
        return clean_body(example["raw_body"], example.get("mime_type"))
    return example["body"]


def _fuzzy_match(actual: str | None, expected: str | None) -> bool:
    if actual is None or expected is None:
        return actual == expected
    return fuzz.token_sort_ratio(actual, expected) >= FUZZY_MATCH_THRESHOLD


def _company_tokens(value: str) -> set[str]:
    return {token for token in normalize_company(value).split() if len(token) >= 2}


def is_junk_company(actual: str | None, expected: str | None) -> bool:
    """An extracted company sharing no word with the labeled one — or any company when
    the email names none. That's the "Com"/"Us" class; a merely imperfect capture
    ("Kestrel Quinlan" for "Kestrel") is wrong but not junk."""
    if not actual:
        return False
    if not expected:
        return True
    return not (_company_tokens(actual) & _company_tokens(expected))


def _has_position(result) -> bool:
    return bool(result.position and result.position.strip())


def _new_bucket() -> dict:
    return {
        "n": 0,
        "tp": 0, "fp": 0, "fn": 0, "tn": 0,
        "status_tp": defaultdict(int), "status_fp": defaultdict(int), "status_fn": defaultdict(int),
        "status_total": 0, "status_correct": 0, "status_confusion": defaultdict(int),
        "company_total": 0, "company_exact": 0, "company_fuzzy": 0, "company_norm": 0, "company_junk": 0,
        "position_total": 0, "position_exact": 0, "position_fuzzy": 0,
        "position_stated": 0, "position_found": 0,
        "cleared_threshold": 0,
        "cleared_threshold_correct": 0,
        "cleared_lenient_correct": 0,
        "true_positive_total": 0,
        "true_positive_cleared": 0,
        "positioned_total": 0,
        "positioned_cleared": 0,
        "review_candidates": 0,
        "review_candidates_not_related": 0,
        "calibration": defaultdict(lambda: [0, 0]),
    }


def _score_one(bucket: dict, result, expected: dict) -> dict[str, bool]:
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

    # fully_correct: the original exact-match definition (synthetic bars use it).
    # lenient_correct: Phase 11's — company compared the way matching compares it,
    # position fuzzily — used for auto_apply_precision and calibration.
    fully_correct = lenient_correct = predicted_related == expected_related

    if expected_related:
        bucket["true_positive_total"] += 1

        expected_status = expected.get("status")
        if expected_status:
            bucket["status_total"] += 1
            status_correct = result.status == expected_status
            bucket["status_correct"] += int(status_correct)
            bucket["status_confusion"][f"{expected_status}->{result.status}"] += 1
            if status_correct:
                bucket["status_tp"][expected_status] += 1
            else:
                bucket["status_fn"][expected_status] += 1
                if result.status:
                    bucket["status_fp"][result.status] += 1
            fully_correct = fully_correct and status_correct
            lenient_correct = lenient_correct and status_correct

        if "company" in expected:
            bucket["company_total"] += 1
            expected_company = expected["company"]
            exact = result.company == expected_company
            normalized = normalize_company(result.company or "") == normalize_company(expected_company or "")
            bucket["company_exact"] += int(exact)
            bucket["company_fuzzy"] += int(exact or _fuzzy_match(result.company, expected_company))
            bucket["company_norm"] += int(normalized)
            bucket["company_junk"] += int(is_junk_company(result.company, expected_company))
            fully_correct = fully_correct and exact
            lenient_correct = lenient_correct and normalized

        if "position" in expected:
            bucket["position_total"] += 1
            expected_position = expected["position"]
            exact = result.position == expected_position
            fuzzy = exact or _fuzzy_match(result.position, expected_position)
            bucket["position_exact"] += int(exact)
            bucket["position_fuzzy"] += int(fuzzy)
            if expected_position is not None:
                bucket["position_stated"] += 1
                bucket["position_found"] += int(_has_position(result))
            fully_correct = fully_correct and exact
            lenient_correct = lenient_correct and fuzzy

    # Mirrors the pipeline: auto-applied only above the threshold AND with a position.
    cleared = predicted_related and result.confidence >= CONFIDENCE_THRESHOLD and _has_position(result)
    if cleared:
        bucket["cleared_threshold"] += 1
        bucket["cleared_threshold_correct"] += int(fully_correct)
        bucket["cleared_lenient_correct"] += int(lenient_correct)
    elif predicted_related:
        bucket["review_candidates"] += 1
        bucket["review_candidates_not_related"] += int(not expected_related)

    if predicted_related:
        band = f"{min(int(result.confidence * 10), 9) / 10:.1f}"
        bucket["calibration"][band][0] += 1
        bucket["calibration"][band][1] += int(lenient_correct)

    if expected_related and cleared:
        bucket["true_positive_cleared"] += 1
    if expected_related and expected.get("position"):
        bucket["positioned_total"] += 1
        bucket["positioned_cleared"] += int(cleared)

    return {"fully_correct": fully_correct, "lenient_correct": lenient_correct, "cleared": cleared}


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
        "status_confusion": dict(sorted(bucket["status_confusion"].items())),  # "expected->predicted": count
        "company_exact_accuracy": round(_safe_div(bucket["company_exact"], bucket["company_total"]), 3),
        "company_fuzzy_accuracy": round(_safe_div(bucket["company_fuzzy"], bucket["company_total"]), 3),
        "company_norm_accuracy": round(_safe_div(bucket["company_norm"], bucket["company_total"]), 3),
        "company_junk_rate": round(_safe_div(bucket["company_junk"], bucket["company_total"]), 3),
        "position_exact_accuracy": round(_safe_div(bucket["position_exact"], bucket["position_total"]), 3),
        "position_fuzzy_accuracy": round(_safe_div(bucket["position_fuzzy"], bucket["position_total"]), 3),
        "position_found_rate": round(_safe_div(bucket["position_found"], bucket["position_stated"]), 3),
        "cleared": bucket["cleared_threshold"],
        "precision_at_threshold": round(_safe_div(bucket["cleared_threshold_correct"], bucket["cleared_threshold"]), 3),
        "auto_apply_precision": round(_safe_div(bucket["cleared_lenient_correct"], bucket["cleared_threshold"]), 3),
        "auto_apply_rate": round(_safe_div(bucket["true_positive_cleared"], bucket["true_positive_total"]), 3),
        "auto_apply_rate_positioned": round(_safe_div(bucket["positioned_cleared"], bucket["positioned_total"]), 3),
        "review_rate": round(_safe_div(bucket["review_candidates"], bucket["true_positive_total"]), 3),
        "queue_false_positive_share": round(
            _safe_div(bucket["review_candidates_not_related"], bucket["review_candidates"]), 3
        ),
        "calibration": {
            band: {"n": n, "accuracy": round(_safe_div(ok, n), 3)}
            for band, (n, ok) in sorted(bucket["calibration"].items())
        },
        "counts": {
            "classification": [tp + tn, bucket["n"]],
            "status": [bucket["status_correct"], bucket["status_total"]],
            "company_exact": [bucket["company_exact"], bucket["company_total"]],
            "company_norm": [bucket["company_norm"], bucket["company_total"]],
            "position_exact": [bucket["position_exact"], bucket["position_total"]],
            "auto_apply_precision": [bucket["cleared_lenient_correct"], bucket["cleared_threshold"]],
        },
    }


def evaluate(extractor, examples: list[dict], *, misses: list | None = None) -> dict[str, Any]:
    """Run `extractor` over `examples`; return overall and per-category metrics as a
    plain, JSON-serializable dict. With `misses`, append one record per example the
    lenient definition gets wrong (local diagnostics — real subjects included, so
    never print these anywhere public)."""
    overall = _new_bucket()
    by_category: dict[str, dict] = defaultdict(_new_bucket)

    for example in examples:
        result = extractor.classify_and_extract(
            subject=example["subject"],
            sender=example["sender"],
            date=example["date"],
            body=example_body(example),
        )
        category = example.get("category", "uncategorized")
        outcome = _score_one(overall, result, example["expected"])
        _score_one(by_category[category], result, example["expected"])
        if misses is not None and not outcome["lenient_correct"]:
            misses.append({
                "ref": example.get("ref"),
                "category": category,
                "subject": example["subject"],
                "sender": example["sender"],
                "expected": example["expected"],
                "predicted": {
                    "is_job_related": result.is_job_related, "status": result.status,
                    "company": result.company, "position": result.position,
                    "confidence": round(result.confidence, 3),
                },
                "reasoning": result.reasoning,
            })

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
    confused = {k: v for k, v in overall["status_confusion"].items() if k.split("->")[0] != k.split("->")[1]}
    print(f"  status confusions (expected->predicted): {confused or 'none'}")
    print(f"company: exact={overall['company_exact_accuracy']} fuzzy={overall['company_fuzzy_accuracy']} "
          f"normalized={overall['company_norm_accuracy']} junk_rate={overall['company_junk_rate']}")
    print(f"position: exact={overall['position_exact_accuracy']} fuzzy={overall['position_fuzzy_accuracy']} "
          f"found_when_stated={overall['position_found_rate']}")
    print(f"cleared={overall['cleared']} precision_at_threshold={overall['precision_at_threshold']} "
          f"auto_apply_precision={overall['auto_apply_precision']}")
    print(f"auto_apply_rate={overall['auto_apply_rate']} auto_apply_rate_positioned={overall['auto_apply_rate_positioned']} "
          f"review_rate={overall['review_rate']} queue_false_positive_share={overall['queue_false_positive_share']}")
    print("calibration (band: n, accuracy): " + ", ".join(
        f"{band}: {m['n']}, {m['accuracy']}" for band, m in overall["calibration"].items()
    ))
    print()
    print("By category:")
    for category, m in sorted(report["by_category"].items()):
        print(
            f"  {category} (n={m['n']}): is_job_related_f1={m['is_job_related']['f1']} "
            f"company_norm={m['company_norm_accuracy']} position_fuzzy={m['position_fuzzy_accuracy']} "
            f"auto_apply_precision={m['auto_apply_precision']} auto_apply_rate={m['auto_apply_rate']}"
        )


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description="Evaluate the classifier.")
    parser.add_argument("--real", action="store_true", help="the private real-mail set (Phase 11)")
    parser.add_argument("--split", choices=("dev", "test", "all"), default="dev")
    parser.add_argument("--final", action="store_true", help="required to score the held-out test split")
    parser.add_argument("--misses", action="store_true", help="print every wrong prediction (local only)")
    parser.add_argument("--json", type=Path, help="also write the report here")
    args = parser.parse_args(argv)

    if args.real:
        if args.split != "dev" and not args.final:
            parser.error("the held-out test split is scored once, at the end of Phase 11 — pass --final")
        from evaluation.real.dataset import load_real_examples
        from evaluation.real.privacy import ensure_outside_repo

        if args.json:
            ensure_outside_repo(args.json)
        examples = load_real_examples(args.split)
    else:
        examples = load_dataset()

    misses: list[dict] | None = [] if args.misses else None
    report = evaluate(RuleBasedExtractor(), examples, misses=misses)
    _print_report(report)
    if misses:
        print("\nMisses:")
        for miss in misses:
            print(json.dumps(miss, ensure_ascii=False))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
