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
_COMPANY_BOUNDARY = r"(?=\s*[.,!]|\s+(?:for|and|regarding|about|which|who)\b|\s*$)"

# Job titles are short, word-shaped strings — never sentence punctuation, never
# many words. Bounded the same way _COMPANY_TOKEN above is bounded, instead of
# the old unbounded, lazy `.+?`. `.+?` is lazy (tries the shortest span first),
# not bounded — it still expands past a short title and across an entire
# sentence whenever the *first* "for the ... position/role" substring in the
# text isn't the real one, e.g. "Thank you for the time you spent with our
# team last week. We would like to invite you to interview for the Senior
# Backend Engineer position at Acme Corp." naively captures everything from
# "the time you spent...for the Senior Backend Engineer" as the "position"
# instead of just "Senior Backend Engineer" — a ~100-character garbage string
# that still gets tier="template" (zero confidence penalty) and auto-applies.
# `/` and `+` are included (alongside the word/company token's other allowed
# punctuation) since real titles like "Software Engineer II" or
# "Full-Stack/Backend" use them.
# `|` is also included (Phase 7): real titles like "Tech Intern | 2027 Summer
# Internship Program" use it as a separator, and the word cap was raised from 6 to 8
# to fit such titles — still bounded, not unbounded, so the overcapture risk this
# comment describes above doesn't reopen.
_POSITION_TOKEN = r"[\w&'/+|\-]+(?:\s[\w&'/+|\-]+){0,7}"

# "you" added to the leading-verb alternation (Task 8 calibration): offer
# emails commonly phrase this as "pleased to offer you the X position at Y"
# — the word directly before "the" is the direct object "you", not one of
# the original for/to/in prepositions, so that phrasing fell through to the
# weaker domain-derived company guess and to no position match at all.
_POSITION_AT_COMPANY_RE = re.compile(
    rf"(?:for|to|in|you|about) the (?P<position>{_POSITION_TOKEN}) "
    rf"(?:position|role|opening|opportunity) at (?P<company>{_COMPANY_TOKEN}){_COMPANY_BOUNDARY}",
    re.IGNORECASE,
)
_APPLICATION_TO_COMPANY_RE = re.compile(
    rf"appl(?:ying|ication) to (?P<company>{_COMPANY_TOKEN}){_COMPANY_BOUNDARY}",
    re.IGNORECASE,
)
_POSITION_ROLE_RE = re.compile(
    rf"for the (?P<position>{_POSITION_TOKEN}) (?:position|role)\b",
    re.IGNORECASE,
)
# "X is thrilled to bring you on board as our new Y" / "as your new Y" — no
# position/role/opening/opportunity keyword present, so neither existing template
# fires; "as (our|your) new" is the only extractable signal.
_POSITION_AS_NEW_RE = re.compile(
    rf"as (?:our|your) new (?P<position>{_POSITION_TOKEN})\b",
    re.IGNORECASE,
)
# Handles a company stated as the sentence's subject rather than reached via "at Y",
# e.g. "Brightview Energy is pleased to extend an offer for the Electrical Engineer
# position." — neither template above fires here since there's no "at <company>".
_COMPANY_PLEASED_TO_OFFER_RE = re.compile(
    rf"(?P<company>{_COMPANY_TOKEN}) (?:is|are) pleased to (?:offer|extend an offer)",
    re.IGNORECASE,
)

# Words that mark the start of a greeting that immediately follows a company mention
# with no intervening punctuation (a common HTML-paragraph-to-text-conversion
# artifact — see the Phase 6 spec's "Anduril Hi Gia Huy" root-cause analysis). Task 6's
# preprocess.py already strips *standalone* greeting lines before this code ever runs;
# this is the second, narrower line of defense for a greeting that survives on the same
# line as real content.
_GREETING_STOPWORDS = {"hi", "hello", "hey", "dear", "greetings"}


def _trim_trailing_greeting(span: str) -> str:
    words = span.split()
    for i, word in enumerate(words):
        if word.lower().strip(",.!") in _GREETING_STOPWORDS:
            return " ".join(words[:i]).strip()
    return span


# When a subject line ends with the company name and the body's next sentence starts
# with it again (e.g. subject "Your offer from Brightview Energy" + body "Brightview
# Energy is pleased to..."), combine_subject_body's single-space join puts the two
# mentions directly adjacent with nothing but a space between them — _COMPANY_TOKEN's
# capitalized-word-run capture (used by every template above) has no way to tell that's
# two mentions of one company rather than one long name, and captures both:
# "Brightview Energy Brightview Energy". This collapses an exact repeated half back
# down to one — safe because a genuine company name being a literal word-for-word
# self-repeat ("Design Design") essentially never happens in practice.
def _dedupe_repeated_span(span: str) -> str:
    words = span.split()
    n = len(words)
    if n > 0 and n % 2 == 0:
        half = n // 2
        if [w.lower() for w in words[:half]] == [w.lower() for w in words[half:]]:
            return " ".join(words[:half])
    return span


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
        return _dedupe_repeated_span(_trim_trailing_greeting(match.group("company").strip(" .,"))), "template"

    match = _APPLICATION_TO_COMPANY_RE.search(text)
    if match:
        return _dedupe_repeated_span(_trim_trailing_greeting(match.group("company").strip(" .,"))), "template"

    match = _COMPANY_PLEASED_TO_OFFER_RE.search(text)
    if match:
        return _dedupe_repeated_span(_trim_trailing_greeting(match.group("company").strip(" .,"))), "template"

    domain_company = _domain_derived_company(sender)
    if domain_company:
        return domain_company, "domain"

    display_name_company = _display_name_derived_company(sender)
    if display_name_company:
        return display_name_company, "display_name"

    return None, "none"


# `sender` is intentionally unused here — kept only for signature symmetry
# with find_company (which does use it), not a bug.
def find_position(text: str, sender: str) -> tuple[str | None, str]:
    match = _POSITION_AT_COMPANY_RE.search(text)
    if match:
        return match.group("position").strip(" .,"), "template"

    match = _POSITION_ROLE_RE.search(text)
    if match:
        return match.group("position").strip(" .,"), "template"

    match = _POSITION_AS_NEW_RE.search(text)
    if match:
        return match.group("position").strip(" .,"), "template"

    return None, "none"
