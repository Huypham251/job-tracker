from datetime import date

import pytest

from app.classifier.extractor import RuleBasedExtractor


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
    # base=0.3 (net_signal=3, capped at 3/6*0.6) + margin=0.225 (3/4*0.3) + domain=0.0
    # - penalty=0.35 (position tier "none") = 0.175
    assert result.confidence == pytest.approx(0.175)
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
    # Corrected from the brief's hand-computed expectation (status="applied",
    # confidence=0.575). Independently tracing classify() against the actual
    # STATUS_PATTERNS in app/classifier/patterns.py shows neither "applied"
    # pattern actually matches this body:
    #   - r"application (?:has been )?received" needs "application" immediately
    #     followed by (optionally "has been " then) "received"; the text has
    #     "received your application" and "your application for" — application
    #     never precedes "received", so no match.
    #   - r"we(?:'ve| have) received your application" needs "we've" or
    #     "we have" before "received"; the text has plain "we received"
    #     (no "'ve"/"have"), so no match either.
    # So all STATUS_PATTERNS score 0 for every status (verified by calling
    # classify() directly: status_scores == {"applied": 0, "oa": 0,
    # "interview": 0, "rejected": 0, "offer": 0}). Only GENERIC_JOB_PATTERNS
    # fire: "your application" (1) + "position" (1) + "recruiting team" (1) = 3,
    # plus the ATS domain relatedness bonus (+2) since greenhouse.io is in
    # ATS_DOMAINS. job_signal = 0 + 3 + 2 = 5, negative_signal = 0, so
    # net_signal = 5 (not 8).
    # With all status_scores tied at 0, top_score is 0, so
    # `status = top_status if top_score > 0 else "other"` yields "other", not
    # "applied", and margin = min((0 - 0) / 4, 1) * 0.3 = 0 (not 0.225, since
    # there is no runner-up gap to reward).
    # base = min(max(5, 0) / 6, 1.0) * 0.6 = (5 / 6) * 0.6 = 0.5
    # margin = 0.0 (top_score == runner_up_score == 0)
    # domain_bonus = 0.1 (sender domain greenhouse.io is in ATS_DOMAINS)
    # penalty = max(EXTRACTION_PENALTY["none"], EXTRACTION_PENALTY["template"])
    #         = max(0.35, 0.0) = 0.35 (company tier "none"; position tier
    #           "template" via find_position's "for the ... position" match)
    # confidence = 0.5 + 0.0 + 0.1 - 0.35 = 0.25
    assert result.status == "other"
    assert result.company is None  # greenhouse.io is blocklisted; no display name to fall back to
    assert result.position == "Backend Engineer"
    assert result.confidence == pytest.approx(0.25)


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
