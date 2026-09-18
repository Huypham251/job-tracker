from datetime import date

import pytest

from app.classifier.extractor import ClassificationError, RuleBasedExtractor


def test_classify_and_extract_marks_marketing_email_not_job_related() -> None:
    extractor = RuleBasedExtractor()

    result = extractor.classify_and_extract(
        subject="50% off sale",
        sender="deals@shop.com",
        date="Mon, 5 Jan 2026 10:00:00 +0000",
        body="Unsubscribe here. View in browser.",
    )

    assert result.is_job_related is False
    assert result.confidence == pytest.approx(0.0)
    assert result.company is None
    assert result.position is None
    assert result.status is None
    assert result.status_date is None


def test_classify_and_extract_applied_email_with_company_but_no_position() -> None:
    extractor = RuleBasedExtractor()

    result = extractor.classify_and_extract(
        subject="",
        sender="careers@acme.com",
        date="Mon, 5 Jan 2026 10:00:00 +0000",
        body="Thank you for applying to Acme Corp.",
    )

    assert result.is_job_related is True
    assert result.status == "applied"
    assert result.company == "Acme Corp"
    assert result.position is None
    # base=0.4 (net_signal=3, min(3/4.5,1)*0.6) + margin=0.3 (min(3/3.0,1)*0.3, capped)
    # + domain=0.0 - penalty=0.35 (position tier "none") = 0.35
    assert result.confidence == pytest.approx(0.35)
    assert result.status_date == date(2026, 1, 5)


def test_classify_and_extract_ats_domain_blocked_from_company_but_boosts_confidence() -> None:
    extractor = RuleBasedExtractor()

    result = extractor.classify_and_extract(
        subject="Acme Corp: Application Update",
        sender="noreply@greenhouse.io",
        date="Mon, 5 Jan 2026 10:00:00 +0000",
        body="We received your application for the Backend Engineer position. Our recruiting team will review it.",
    )

    assert result.is_job_related is True
    # Corrected 2026-09-16 (final review finding #3): the "applied" pattern
    # r"we(?:'ve| have) received your application" required the auxiliary
    # verb ("we've"/"we have"), so plain "we received your application" (as
    # in this body) matched NO applied pattern — the earlier expected values
    # below (status="other", confidence=0.25) were only correct because of
    # that pattern gap, not because they were the "right" answer. The pattern
    # was broadened to r"(?:we|i)(?:'ve| have)? received your application",
    # which now matches this body. Independently tracing classify() against
    # the current app/classifier/patterns.py:
    #   - the broadened "applied" pattern matches "we received your
    #     application" -> status_scores["applied"] = 3, job_signal += 3.
    #   - r"application (?:has been )?received" still doesn't match (the text
    #     has "your application for", never "application received").
    # GENERIC_JOB_PATTERNS fire as before: "your application" (1) +
    # "position" (1) + "recruiting team" (1) = 3, plus the ATS domain
    # relatedness bonus (+2) since greenhouse.io is in ATS_DOMAINS.
    # job_signal = 3 + 3 + 2 = 8, negative_signal = 0, net_signal = 8 (back to
    # the plan brief's original hand-computed value).
    # With status_scores == {"applied": 3, "oa": 0, "interview": 0,
    # "rejected": 0, "offer": 0}, top_score = 3 > 0, so status = "applied",
    # and margin = min((3 - 0) / 4, 1) * 0.3 = 0.225.
    # base = min(max(8, 0) / 4.5, 1.0) * 0.6 = min(1.778, 1.0) * 0.6 = 0.6 (still capped)
    # margin = min(3 / 3.0, 1.0) * 0.3 = 0.3 (now also capped, was 0.225 under the old
    #   MARGIN_NORM=4.0 — Task 10 lowered it to 3.0)
    # domain_bonus = 0.1 (sender domain greenhouse.io is in ATS_DOMAINS)
    # penalty = max(EXTRACTION_PENALTY["none"], EXTRACTION_PENALTY["template"])
    #         = max(0.35, 0.0) = 0.35 (company tier "none"; position tier
    #           "template" via find_position's "for the ... position" match)
    # confidence = 0.6 + 0.3 + 0.1 - 0.35 = 0.65
    assert result.status == "applied"
    assert result.company is None  # greenhouse.io is blocklisted; no display name to fall back to
    assert result.position == "Backend Engineer"
    assert result.confidence == pytest.approx(0.65)


def test_classify_and_extract_high_confidence_interview_with_both_fields() -> None:
    extractor = RuleBasedExtractor()

    result = extractor.classify_and_extract(
        subject="Interview Invitation",
        sender="careers@acme.com",
        date="Tue, 6 Jan 2026 09:00:00 +0000",
        body="We would like to invite you to interview for the Backend Engineer position at Acme Corp.",
    )

    assert result.is_job_related is True
    assert result.status == "interview"
    assert result.company == "Acme Corp"
    assert result.position == "Backend Engineer"
    # base=0.6 (capped) + margin=0.3 (capped) + domain=0.0 - penalty=0.0 = 0.9
    assert result.confidence == pytest.approx(0.9)
    assert result.status_date == date(2026, 1, 6)


def test_classify_and_extract_all_zero_signal_email_is_not_job_related() -> None:
    extractor = RuleBasedExtractor()

    result = extractor.classify_and_extract(
        subject="Happy birthday!",
        sender="friend@example.com",
        date="Mon, 5 Jan 2026 10:00:00 +0000",
        body="Hope you have a wonderful day.",
    )

    assert result.is_job_related is False
    assert result.confidence == pytest.approx(0.0)


def test_classify_and_extract_raises_classification_error_not_validation_error_on_oversized_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression test for final review finding #2: an over-length captured
    # field (e.g. company/position exceeding EmailExtraction's max_length=255,
    # possible even after the #1 fix as defense in depth against a future
    # regex or a BODY_MAX_CHARS-truncated body) must not raise a raw
    # pydantic.ValidationError out of classify_and_extract — that exception
    # type isn't caught by app/pipeline/service.py's
    # `except (GoogleApiError, ClassificationError)`, so it would propagate
    # as an uncaught 500 and, because the message is never persisted as a
    # ProcessedMessage row, permanently wedge every future "Process Inbox"
    # click on the same message.
    import app.classifier.extractor as extractor_module

    oversized = "A" * 300
    monkeypatch.setattr(extractor_module, "find_company", lambda text, sender: (oversized, "template"))

    extractor = RuleBasedExtractor()

    with pytest.raises(ClassificationError):
        extractor.classify_and_extract(
            subject="Interview Invitation",
            sender="careers@acme.com",
            date="Tue, 6 Jan 2026 09:00:00 +0000",
            body="We would like to invite you to interview for the Backend Engineer position at Acme Corp.",
        )
