import re

from app.classifier.patterns import ATS_DOMAINS
from app.classifier.text import extract_sender_display_name, extract_sender_domain

_ROLE_SUFFIX_WORDS = {"recruiting", "talent", "careers", "hr", "team"}

# Company names are captured as 1-5 whitespace-separated "word" tokens (letters,
# digits, &, ', - — deliberately NOT '.', so "Corp." / "Inc." naturally end the
# token at the period rather than swallowing it), bounded by a lookahead that
# stops at sentence punctuation, a small set of common connector words, or the
# end of the string. Without this, a lazy quantifier ending only at the next
# [.,!] overcaptures straight through mid-sentence company mentions like
# "...application to Zeta Co for the Marketing Analyst position." (naively
# capturing "Zeta Co for the Marketing Analyst position" instead of "Zeta Co").
# The quantifier is greedy (tries the longest span first) but Python's regex
# engine backtracks down to the shortest span that satisfies the lookahead, so
# it still finds the correct short boundary — verified by hand against every
# example in evaluation/dataset.jsonl (Task 7) before relying on it here.
_COMPANY_TOKEN = r"[\w&'\-]+(?:\s[\w&'\-]+){0,4}"
_COMPANY_BOUNDARY = r"(?=[.,!]|\s+(?:for|and|regarding|about|which|who)\b|\s*$)"

_POSITION_AT_COMPANY_RE = re.compile(
    rf"(?:for|to|in) the (?P<position>.+?) (?:position|role) at (?P<company>{_COMPANY_TOKEN}){_COMPANY_BOUNDARY}",
    re.IGNORECASE,
)
_APPLICATION_TO_COMPANY_RE = re.compile(
    rf"appl(?:ying|ication) to (?P<company>{_COMPANY_TOKEN}){_COMPANY_BOUNDARY}",
    re.IGNORECASE,
)
_POSITION_ROLE_RE = re.compile(
    r"for the (?P<position>.+?) (?:position|role)\b",
    re.IGNORECASE,
)


def _domain_derived_company(sender: str) -> str | None:
    domain = extract_sender_domain(sender)
    if not domain or domain in ATS_DOMAINS:
        return None
    label = domain.split(".")[0]
    return label.capitalize() if label else None


def _display_name_derived_company(sender: str) -> str | None:
    display_name = extract_sender_display_name(sender)
    if not display_name:
        return None
    words = [w for w in display_name.split() if w.lower() not in _ROLE_SUFFIX_WORDS]
    cleaned = " ".join(words).strip()
    return cleaned or None


def find_company(text: str, sender: str) -> tuple[str | None, str]:
    match = _POSITION_AT_COMPANY_RE.search(text)
    if match:
        return match.group("company").strip(" .,"), "template"

    match = _APPLICATION_TO_COMPANY_RE.search(text)
    if match:
        return match.group("company").strip(" .,"), "template"

    domain_company = _domain_derived_company(sender)
    if domain_company:
        return domain_company, "domain"

    display_name_company = _display_name_derived_company(sender)
    if display_name_company:
        return display_name_company, "display_name"

    return None, "none"


def find_position(text: str, sender: str) -> tuple[str | None, str]:
    match = _POSITION_AT_COMPANY_RE.search(text)
    if match:
        return match.group("position").strip(" .,"), "template"

    match = _POSITION_ROLE_RE.search(text)
    if match:
        return match.group("position").strip(" .,"), "template"

    return None, "none"
