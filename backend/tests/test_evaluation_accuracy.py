"""Runs the classifier against evaluation/dataset.jsonl as part of the normal test
suite. Local classification costs nothing, so this is a real, always-on regression
gate: a weight/pattern/threshold change in app/classifier/ that regresses accuracy
fails this test immediately.

Uses evaluate() from evaluation/run_eval.py (the same function evaluation/compare.py
and evaluation/inspect_confidence.py rely on) instead of re-implementing scoring here —
Phase 6 added enough metrics (precision_at_threshold, auto_apply_rate, fuzzy matching,
per-category breakdowns) that a second hand-rolled copy would drift.

Every bar below is set to genuinely tolerate one additional miss beyond the dataset's
current numbers (not just barely clear them) — see evaluate()'s docstring in
run_eval.py for the metric definitions, and docs/superpowers/plans/
2026-09-17-job-tracker-phase-6-classifier-quality.md Task 11 for the exact procedure
used to pick these values from a real `uv run python -m evaluation.run_eval` run
against the 88-example dataset (18 original clean_template examples + 70 added across
html_noise, greeting_adjacent, signature_footer, recruiter_outreach, ambiguous,
sender_variation, and messy_phrasing).
"""

from app.classifier.extractor import RuleBasedExtractor
from evaluation.run_eval import CONFIDENCE_THRESHOLD, evaluate, load_dataset

MIN_CLASSIFICATION_ACCURACY = 0.97  # currently 87/88=0.989; tolerates 86/88=0.977; fails at 85/88=0.966
MIN_STATUS_ACCURACY = 0.98  # currently 72/72=1.0; tolerates 71/72=0.986; fails at 70/72=0.972
MIN_COMPANY_ACCURACY = 0.74  # exact-match, same as Phase 4b (not fuzzy); currently 55/72=0.764; tolerates 54/72=0.75; fails at 53/72=0.736
MIN_POSITION_ACCURACY = 0.92  # exact-match, same as Phase 4b (not fuzzy); currently 61/65=0.938; tolerates 60/65=0.923; fails at 59/65=0.908
MIN_PRECISION_AT_THRESHOLD = 0.90  # guards real auto-applies — the most important bar; currently 26/27=0.963; tolerates 25/27=0.926; fails at 24/27=0.889
MIN_AUTO_APPLY_RATE = 0.36  # a floor: confirms Task 10 didn't silently regress; currently 27/72=0.375; tolerates 26/72=0.361; fails at 25/72=0.347


def test_classification_accuracy_meets_minimum_bar() -> None:
    report = evaluate(RuleBasedExtractor(), load_dataset())["overall"]

    assert report["classification_accuracy"] >= MIN_CLASSIFICATION_ACCURACY, (
        f"classification accuracy {report['classification_accuracy']:.2f} fell below "
        f"the {MIN_CLASSIFICATION_ACCURACY} bar"
    )
    assert report["status_accuracy"] >= MIN_STATUS_ACCURACY, (
        f"status accuracy {report['status_accuracy']:.2f} fell below the {MIN_STATUS_ACCURACY} bar"
    )
    assert report["company_exact_accuracy"] >= MIN_COMPANY_ACCURACY, (
        f"company accuracy {report['company_exact_accuracy']:.2f} fell below the {MIN_COMPANY_ACCURACY} bar"
    )
    assert report["position_exact_accuracy"] >= MIN_POSITION_ACCURACY, (
        f"position accuracy {report['position_exact_accuracy']:.2f} fell below the {MIN_POSITION_ACCURACY} bar"
    )


def test_precision_at_threshold_and_auto_apply_rate_meet_minimum_bar() -> None:
    """The Phase 6 metrics that guard and measure the auto-apply trust-model gate
    directly (spec §6) — kept as a separate test from classification/extraction
    accuracy above so a failure message immediately says which kind of regression
    happened."""
    report = evaluate(RuleBasedExtractor(), load_dataset())["overall"]

    assert report["precision_at_threshold"] >= MIN_PRECISION_AT_THRESHOLD, (
        f"precision_at_threshold {report['precision_at_threshold']:.2f} fell below "
        f"the {MIN_PRECISION_AT_THRESHOLD} bar — this guards real auto-applies, do not "
        f"lower it without new evidence"
    )
    assert report["auto_apply_rate"] >= MIN_AUTO_APPLY_RATE, (
        f"auto_apply_rate {report['auto_apply_rate']:.2f} fell below the {MIN_AUTO_APPLY_RATE} bar"
    )


def test_confidence_threshold_constant_matches_settings() -> None:
    """evaluation/run_eval.py's CONFIDENCE_THRESHOLD is a deliberate duplicate of
    settings.classification_confidence_threshold (see run_eval.py's comment for why it
    isn't imported directly). This test is what keeps the two from silently drifting
    apart if one is ever changed without the other."""
    from app.core.config import settings

    assert CONFIDENCE_THRESHOLD == settings.classification_confidence_threshold
