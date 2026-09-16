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
#
# Each word must start with an uppercase letter (Task 8 calibration): subject
# and body are joined with a single space and no sentence-ending punctuation
# between them (see text.combine_subject_body), so when a subject itself
# matches one of these templates (e.g. "Your application to Acme Corp") with
# nothing terminating it before the body's next sentence begins, the old
# all-word-chars token could swallow the body's leading words too (e.g.
# "Acme Corp Thank you" — "Thank you" is a real lowercase-tailed clause that
# only stops matching once "you" breaks the per-word capital requirement).
# Company names are capitalized in every example in the dataset, and English
# sentence-internal words following a company mention are effectively never
# an all-capitalized run, so this filters out the overcapture while still
# matching every legitimate multi-word company name observed. The `(?-i:...)`
# group turns off the surrounding pattern's re.IGNORECASE just for this
# capitalization check (IGNORECASE would otherwise make [A-Z] match lowercase
# too, defeating the point).
_COMPANY_TOKEN = r"(?-i:[A-Z][\w&'\-]*(?:\s[A-Z][\w&'\-]*){0,4})"
_COMPANY_BOUNDARY = r"(?=[.,!]|\s+(?:for|and|regarding|about|which|who)\b|\s*$)"

# "you" added to the leading-verb alternation (Task 8 calibration): offer
# emails commonly phrase this as "pleased to offer you the X position at Y"
# — the word directly before "the" is the direct object "you", not one of
# the original for/to/in prepositions, so that phrasing fell through to the
# weaker domain-derived company guess and to no position match at all.
_POSITION_AT_COMPANY_RE = re.compile(
    rf"(?:for|to|in|you) the (?P<position>.+?) (?:position|role) at (?P<company>{_COMPANY_TOKEN}){_COMPANY_BOUNDARY}",
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
