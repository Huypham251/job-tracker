# backend/tests/test_evaluation_run_eval.py
import pytest

from app.classifier.schemas import EmailExtraction
from evaluation.run_eval import evaluate, is_junk_company, main


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
        EmailExtraction(is_job_related=True, confidence=0.9, status="applied", position="SWE"),  # clears, correct
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


class _RecordingExtractor:
    def __init__(self, result: EmailExtraction) -> None:
        self.result = result
        self.bodies: list[str] = []

    def classify_and_extract(self, *, subject, sender, date, body) -> EmailExtraction:
        self.bodies.append(body)
        return self.result


def test_real_examples_are_cleaned_by_the_production_function() -> None:
    extractor = _RecordingExtractor(EmailExtraction(is_job_related=False, confidence=0.0))
    example = {
        "subject": "s", "sender": "a@a.com", "date": "Mon, 1 Jan 2026 00:00:00 +0000",
        "raw_body": "<p>Hello <b>World</b></p>", "mime_type": "text/html",
        "expected": {"is_job_related": False},
    }
    evaluate(extractor, [example])
    assert extractor.bodies == ["Hello World"]


@pytest.mark.parametrize(
    "actual, expected, junk",
    [
        ("Us", "Kestrel IQ", True),
        ("Com", None, True),
        ("Kestrel IQ Inc", "Kestrel IQ", False),
        ("Kestrel Quinlan", "Kestrel", False),  # imperfect, not junk
        (None, "Kestrel", False),
    ],
)
def test_is_junk_company(actual, expected, junk) -> None:
    assert is_junk_company(actual, expected) is junk


def test_normalized_company_accuracy_and_junk_rate() -> None:
    examples = [
        _example({"is_job_related": True, "status": "applied", "company": "Acme"}),
        _example({"is_job_related": True, "status": "applied", "company": "Kestrel IQ"}),
    ]
    results = [
        EmailExtraction(is_job_related=True, confidence=0.5, status="applied", company="Acme, Inc."),
        EmailExtraction(is_job_related=True, confidence=0.5, status="applied", company="Us"),
    ]
    overall = evaluate(_StubExtractor(results), examples)["overall"]
    assert overall["company_exact_accuracy"] == 0.0
    assert overall["company_norm_accuracy"] == 0.5
    assert overall["company_junk_rate"] == 0.5


def test_a_confident_prediction_without_a_position_is_not_cleared() -> None:
    examples = [_example({"is_job_related": True, "status": "applied", "company": "Acme", "position": "SWE"})]
    results = [EmailExtraction(is_job_related=True, confidence=0.95, status="applied", company="Acme")]
    overall = evaluate(_StubExtractor(results), examples)["overall"]
    assert overall["cleared"] == 0
    assert overall["review_rate"] == 1.0
    assert overall["position_found_rate"] == 0.0
    assert overall["auto_apply_rate_positioned"] == 0.0


def test_auto_apply_precision_is_lenient_about_company_suffixes_and_position_punctuation() -> None:
    examples = [_example({"is_job_related": True, "status": "applied", "company": "Acme", "position": "Software Engineer"})]
    results = [EmailExtraction(is_job_related=True, confidence=0.9, status="applied",
                               company="Acme Inc.", position="Software Engineer.")]
    overall = evaluate(_StubExtractor(results), examples)["overall"]
    assert overall["precision_at_threshold"] == 0.0  # exact definition, unchanged
    assert overall["auto_apply_precision"] == 1.0
    assert overall["counts"]["auto_apply_precision"] == [1, 1]
    assert overall["calibration"] == {"0.9": {"n": 1, "accuracy": 1.0}}
    assert overall["status_confusion"] == {"applied->applied": 1}


def test_queue_false_positive_share() -> None:
    examples = [_example({"is_job_related": False}), _example({"is_job_related": True, "status": "applied"})]
    results = [EmailExtraction(is_job_related=True, confidence=0.4, status="applied"),
               EmailExtraction(is_job_related=True, confidence=0.4, status="applied")]
    assert evaluate(_StubExtractor(results), examples)["overall"]["queue_false_positive_share"] == 0.5


def test_misses_collects_only_wrong_predictions() -> None:
    examples = [_example({"is_job_related": True}), _example({"is_job_related": False})]
    results = [EmailExtraction(is_job_related=True, confidence=0.9),
               EmailExtraction(is_job_related=True, confidence=0.9)]
    misses: list[dict] = []
    evaluate(_StubExtractor(results), examples, misses=misses)
    assert len(misses) == 1 and misses[0]["expected"] == {"is_job_related": False}


def test_cli_refuses_the_held_out_split_without_final() -> None:
    with pytest.raises(SystemExit):
        main(["--real", "--split", "test"])
