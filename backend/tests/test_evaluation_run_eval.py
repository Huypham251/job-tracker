# backend/tests/test_evaluation_run_eval.py
from app.classifier.schemas import EmailExtraction
from evaluation.run_eval import evaluate


class _StubExtractor:
    """Returns pre-built EmailExtraction results in order, ignoring its inputs —
    lets these tests assert evaluate()'s arithmetic against known ground truth without
    depending on RuleBasedExtractor's actual behavior."""

    def __init__(self, results: list[EmailExtraction]) -> None:
        self._results = iter(results)

    def classify_and_extract(self, *, subject, sender, date, body) -> EmailExtraction:
        return next(self._results)


def _example(expected: dict) -> dict:
    return {
        "subject": "s", "sender": "a@a.com", "date": "Mon, 1 Jan 2026 00:00:00 +0000",
        "body": "b", "expected": expected,
    }


def test_evaluate_computes_precision_recall_f1_for_is_job_related() -> None:
    examples = [
        _example({"is_job_related": True}),
        _example({"is_job_related": True}),
        _example({"is_job_related": False}),
        _example({"is_job_related": False}),
    ]
    results = [
        EmailExtraction(is_job_related=True, confidence=0.9),   # TP
        EmailExtraction(is_job_related=False, confidence=0.1),  # FN
        EmailExtraction(is_job_related=True, confidence=0.9),   # FP
        EmailExtraction(is_job_related=False, confidence=0.1),  # TN
    ]
    report = evaluate(_StubExtractor(results), examples)
    assert report["overall"]["is_job_related"] == {
        "precision": 0.5, "recall": 0.5, "f1": 0.5, "tp": 1, "fp": 1, "fn": 1, "tn": 1,
    }


def test_evaluate_fuzzy_company_match_tolerates_trailing_punctuation() -> None:
    examples = [_example({"is_job_related": True, "company": "Acme Corp", "status": "applied"})]
    results = [EmailExtraction(is_job_related=True, confidence=0.9, company="Acme Corp.", status="applied")]
    report = evaluate(_StubExtractor(results), examples)
    overall = report["overall"]
    assert overall["company_exact_accuracy"] == 0.0
    assert overall["company_fuzzy_accuracy"] == 1.0


def test_evaluate_computes_threshold_and_rate_metrics() -> None:
    examples = [
        _example({"is_job_related": True, "status": "applied"}),
        _example({"is_job_related": True, "status": "applied"}),
    ]
    results = [
        EmailExtraction(is_job_related=True, confidence=0.9, status="applied"),  # clears, correct
        EmailExtraction(is_job_related=True, confidence=0.5, status="applied"),  # below threshold
    ]
    report = evaluate(_StubExtractor(results), examples)
    overall = report["overall"]
    assert overall["precision_at_threshold"] == 1.0
    assert overall["auto_apply_rate"] == 0.5
    assert overall["review_rate"] == 0.5


def test_evaluate_breaks_out_metrics_by_category() -> None:
    examples = [
        {**_example({"is_job_related": True}), "category": "cat_a"},
        {**_example({"is_job_related": False}), "category": "cat_b"},
    ]
    results = [
        EmailExtraction(is_job_related=True, confidence=0.9),
        EmailExtraction(is_job_related=False, confidence=0.1),
    ]
    report = evaluate(_StubExtractor(results), examples)
    assert set(report["by_category"]) == {"cat_a", "cat_b"}
    assert report["by_category"]["cat_a"]["is_job_related"]["tp"] == 1
    assert report["by_category"]["cat_b"]["is_job_related"]["tn"] == 1


def test_evaluate_computes_overall_classification_and_status_accuracy() -> None:
    examples = [
        _example({"is_job_related": True, "status": "applied"}),
        _example({"is_job_related": True, "status": "rejected"}),
        _example({"is_job_related": False}),
    ]
    results = [
        EmailExtraction(is_job_related=True, confidence=0.9, status="applied"),   # correct
        EmailExtraction(is_job_related=True, confidence=0.9, status="interview"),  # wrong status
        EmailExtraction(is_job_related=False, confidence=0.1),                     # correct
    ]
    report = evaluate(_StubExtractor(results), examples)
    overall = report["overall"]
    assert overall["classification_accuracy"] == 1.0  # all 3 is_job_related predictions correct
    assert overall["status_accuracy"] == 0.5           # 1 of 2 true-positive statuses correct
