# backend/evaluation/compare.py
"""Diff current metrics against a frozen baseline.

    cd backend
    uv run python -m evaluation.compare           # synthetic examples vs evaluation/baseline_metrics.json (Phase 6)
    uv run python -m evaluation.compare --real    # private real dev split vs <private dir>/real_baseline.json (Phase 11 CP0)
"""

import argparse
import json
from pathlib import Path
from typing import Any

from app.classifier.extractor import RuleBasedExtractor
from evaluation.run_eval import evaluate, load_dataset, synthetic_examples

BASELINE_PATH = Path(__file__).parent / "baseline_metrics.json"

_TRACKED_METRICS: list[tuple[str, ...]] = [
    ("is_job_related", "precision"),
    ("is_job_related", "recall"),
    ("is_job_related", "f1"),
    ("status_accuracy",),
    ("company_exact_accuracy",),
    ("company_fuzzy_accuracy",),
    ("company_norm_accuracy",),
    ("company_junk_rate",),
    ("position_exact_accuracy",),
    ("position_fuzzy_accuracy",),
    ("position_found_rate",),
    ("precision_at_threshold",),
    ("auto_apply_precision",),
    ("auto_apply_rate",),
    ("auto_apply_rate_positioned",),
    ("review_rate",),
    ("queue_false_positive_share",),
]
_LOWER_IS_BETTER = {"company_junk_rate", "review_rate", "queue_false_positive_share"}


def _get(section: dict, path: tuple[str, ...]) -> Any:
    value: Any = section
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _fmt(value: Any) -> str:
    return f"{value:>10.3f}" if isinstance(value, (int, float)) else f"{'n/a':>10}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compare metrics against a frozen baseline.")
    parser.add_argument("--real", action="store_true", help="private real dev split (Phase 11)")
    args = parser.parse_args(argv)

    if args.real:
        from evaluation.real.dataset import load_real_examples
        from evaluation.real.privacy import private_dir

        baseline_path = private_dir() / "real_baseline.json"
        examples = load_real_examples("dev")
    else:
        baseline_path = BASELINE_PATH
        examples = synthetic_examples(load_dataset())

    if not baseline_path.exists():
        raise SystemExit(f"{baseline_path} does not exist yet — nothing to compare against.")
    baseline = json.loads(baseline_path.read_text())
    current = evaluate(RuleBasedExtractor(), examples)

    print(f"{'metric':<30} {'baseline':>10} {'current':>10} {'delta':>10}")
    for path in _TRACKED_METRICS:
        label = ".".join(path)
        b, c = _get(baseline["overall"], path), _get(current["overall"], path)
        if isinstance(b, (int, float)) and isinstance(c, (int, float)):
            delta = c - b
            worse = delta > 0.001 if path[-1] in _LOWER_IS_BETTER else delta < -0.001
            print(f"{label:<30} {_fmt(b)} {_fmt(c)} {delta:>+10.3f}{'  REGRESSION' if worse else ''}")
        else:
            print(f"{label:<30} {_fmt(b)} {_fmt(c)} {'':>10}")

    print()
    print(f"{'category':<24} {'is_job_related_f1':>20} {'auto_apply_rate':>18} {'precision_at_threshold':>24}")
    for category in sorted(current["by_category"]):
        cur = current["by_category"][category]
        base = baseline["by_category"].get(category, {})
        print(
            f"{category:<24} "
            f"{cur['is_job_related']['f1']:>10.3f} (was {base.get('is_job_related', {}).get('f1', 'n/a')})  "
            f"{cur['auto_apply_rate']:>8.3f} (was {base.get('auto_apply_rate', 'n/a')})  "
            f"{cur['precision_at_threshold']:>10.3f} (was {base.get('precision_at_threshold', 'n/a')})"
        )


if __name__ == "__main__":
    main()
