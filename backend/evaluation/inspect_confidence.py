# backend/evaluation/inspect_confidence.py
"""Diagnostic tool for Task 10 (confidence recalibration): prints the confidence-
formula's components for every job-related dataset example, so it's visible where
JOB_SIGNAL_NORM/MARGIN_NORM are leaving real signal on the table for realistically-
phrased (non-template) mail. Not a regression gate — evaluation/compare.py is that.
Duplicates extractor.py's confidence formula deliberately (a diagnostic script, not
production code) — if RuleBasedExtractor's formula structure ever changes (not just its
constants), this script's formula must be updated to match or its output is meaningless.

Run: cd backend && uv run python -m evaluation.inspect_confidence
"""

from app.classifier.extractor import classify
from app.classifier.fields import find_company, find_position
from app.classifier.patterns import (
    ATS_DOMAINS,
    DOMAIN_CONFIDENCE_BONUS,
    EXTRACTION_PENALTY,
    JOB_SIGNAL_NORM,
    MARGIN_NORM,
)
from app.classifier.preprocess import preprocess_body
from app.classifier.text import combine_subject_body, extract_sender_domain, normalize_text
from evaluation.run_eval import load_dataset


def main() -> None:
    for example in load_dataset():
        expected = example["expected"]
        if not expected.get("is_job_related"):
            continue

        subject, sender, body = example["subject"], example["sender"], example["body"]
        body = preprocess_body(body)
        text = normalize_text(subject, body)
        sender_domain = extract_sender_domain(sender)
        status_scores, job_signal, negative_signal = classify(text, sender_domain)
        net_signal = job_signal - negative_signal

        ranked = sorted(status_scores.items(), key=lambda kv: kv[1], reverse=True)
        top_score, runner_up_score = ranked[0][1], ranked[1][1]

        raw_text = combine_subject_body(subject, body)
        _, company_tier = find_company(raw_text, sender)
        _, position_tier = find_position(raw_text, sender)

        base = min(max(net_signal, 0) / JOB_SIGNAL_NORM, 1.0) * 0.6
        margin = min((top_score - runner_up_score) / MARGIN_NORM, 1.0) * 0.3
        domain_bonus = DOMAIN_CONFIDENCE_BONUS if sender_domain in ATS_DOMAINS else 0.0
        penalty = max(EXTRACTION_PENALTY[company_tier], EXTRACTION_PENALTY[position_tier])
        confidence = max(0.0, min(1.0, base + margin + domain_bonus - penalty))

        print(
            f"{example.get('category', '?'):<20} net_signal={net_signal:>3} "
            f"base={base:.3f} margin={margin:.3f} domain_bonus={domain_bonus:.2f} "
            f"penalty={penalty:.2f} -> confidence={confidence:.3f}"
        )


if __name__ == "__main__":
    main()
