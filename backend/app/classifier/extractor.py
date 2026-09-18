import re
from typing import Protocol

from pydantic import ValidationError

from app.classifier.fields import find_company, find_position
from app.classifier.patterns import (
    ATS_DOMAINS,
    DOMAIN_CONFIDENCE_BONUS,
    DOMAIN_RELATEDNESS_BONUS,
    EXTRACTION_PENALTY,
    GENERIC_JOB_PATTERNS,
    JOB_RELATED_THRESHOLD,
    JOB_SIGNAL_NORM,
    MARGIN_NORM,
    NEGATIVE_PATTERNS,
    STATUS_PATTERNS,
)
from app.classifier.preprocess import preprocess_body
from app.classifier.schemas import EmailExtraction
from app.classifier.text import combine_subject_body, extract_sender_domain, normalize_text, parse_email_date


class Extractor(Protocol):
    def classify_and_extract(
        self, *, subject: str, sender: str, date: str, body: str
    ) -> EmailExtraction: ...


class ClassificationError(Exception):
    """Raised only on a malformed/unusable input the classifier cannot process."""


def classify(text: str, sender_domain: str) -> tuple[dict[str, int], int, int]:
    """Score `text` (already normalized: lowercased, whitespace-collapsed) against the
    pattern tables. Returns (status_scores, job_signal, negative_signal)."""
    status_scores = {status: 0 for status in STATUS_PATTERNS}
    job_signal = 0

    for status, patterns in STATUS_PATTERNS.items():
        for pattern, weight in patterns:
            if re.search(pattern, text):
                status_scores[status] += weight
                job_signal += weight

    for pattern, weight in GENERIC_JOB_PATTERNS:
        if re.search(pattern, text):
            job_signal += weight

    negative_signal = sum(weight for pattern, weight in NEGATIVE_PATTERNS if re.search(pattern, text))

    if sender_domain in ATS_DOMAINS:
        job_signal += DOMAIN_RELATEDNESS_BONUS

    return status_scores, job_signal, negative_signal


def _build_extraction(**kwargs) -> EmailExtraction:
    """Construct EmailExtraction, translating a pydantic validation failure (e.g. a
    captured company/position exceeding max_length=255) into ClassificationError.

    Without this, pydantic.ValidationError propagates straight out of
    classify_and_extract. That's not ClassificationError, so
    app/pipeline/service.py's `except (GoogleApiError, ClassificationError)` doesn't
    catch it: the exception escapes the route as an uncaught 500, the message never
    gets written as a ProcessedMessage row, and every future "Process Inbox" click
    hits the same message and 500s again — a permanently wedged inbox. Raising
    ClassificationError instead lets the pipeline's existing handling skip just this
    one message and continue the batch.
    """
    try:
        return EmailExtraction(**kwargs)
    except ValidationError as exc:
        raise ClassificationError(f"invalid extraction fields: {exc}") from exc


class RuleBasedExtractor:
    def classify_and_extract(
        self, *, subject: str, sender: str, date: str, body: str
    ) -> EmailExtraction:
        body = preprocess_body(body)
        text = normalize_text(subject, body)
        sender_domain = extract_sender_domain(sender)
        status_scores, job_signal, negative_signal = classify(text, sender_domain)
        net_signal = job_signal - negative_signal
        is_job_related = net_signal >= JOB_RELATED_THRESHOLD

        base = min(max(net_signal, 0) / JOB_SIGNAL_NORM, 1.0) * 0.6

        if not is_job_related:
            confidence = max(0.0, min(1.0, base))
            return _build_extraction(is_job_related=False, confidence=confidence)

        ranked = sorted(status_scores.items(), key=lambda kv: kv[1], reverse=True)
        top_status, top_score = ranked[0]
        runner_up_score = ranked[1][1]
        status = top_status if top_score > 0 else "other"

        raw_text = combine_subject_body(subject, body)
        company, company_tier = find_company(raw_text, sender)
        position, position_tier = find_position(raw_text, sender)

        margin = min((top_score - runner_up_score) / MARGIN_NORM, 1.0) * 0.3
        domain_bonus = DOMAIN_CONFIDENCE_BONUS if sender_domain in ATS_DOMAINS else 0.0
        penalty = max(EXTRACTION_PENALTY[company_tier], EXTRACTION_PENALTY[position_tier])
        confidence = max(0.0, min(1.0, base + margin + domain_bonus - penalty))

        reasoning = (
            f"status={status} (score={top_score}, runner_up={runner_up_score}); "
            f"company via {company_tier}; position via {position_tier}"
        )

        return _build_extraction(
            is_job_related=True,
            confidence=confidence,
            company=company,
            position=position,
            status=status,
            status_date=parse_email_date(date),
            reasoning=reasoning,
        )
