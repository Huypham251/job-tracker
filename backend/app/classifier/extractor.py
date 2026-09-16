import re
from typing import Protocol

from app.classifier.patterns import (
    ATS_DOMAINS,
    DOMAIN_RELATEDNESS_BONUS,
    GENERIC_JOB_PATTERNS,
    NEGATIVE_PATTERNS,
    STATUS_PATTERNS,
)
from app.classifier.schemas import EmailExtraction


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
