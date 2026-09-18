import re

# A line that is ONLY a greeting (optionally with a short name/title after it) — not a
# sentence that merely starts with one of these words. Requires the line to end in a
# literal comma or colon (mandatory, not optional) within 60 chars of the greeting
# word, so a real sentence like "Hi-tech companies are hiring fast." (ends in a
# period, not a comma/colon) or "Dear applicant, your application has been rejected."
# (a comma appears mid-line, not at the end) is correctly left alone.
_GREETING_LINE_RE = re.compile(
    r"^[ \t]*(?:hi|hello|hey|dear|greetings)\b[^\n]{0,60}[,:][ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# A line that is just a sign-off word/phrase (optionally with trailing punctuation) —
# everything from that line to the end of the body is a signature/footer block and is
# dropped. "--" alone is the conventional plain-text signature delimiter.
_SIGNATURE_START_RE = re.compile(
    r"^[ \t]*(?:--[ \t]*$|(?:best regards|warm regards|kind regards|many thanks|"
    r"congratulations(?: again)?|best|regards|sincerely|thanks|thank you)[,.]?[ \t]*$)",
    re.IGNORECASE | re.MULTILINE,
)

# Smart quotes/dashes that real email clients commonly introduce, which the literal-
# ASCII patterns in patterns.py (e.g. "received your application", built with a plain
# apostrophe) otherwise silently fail to match. Verified: "we've received your
# application" matches that pattern; "we’ve received your application" (curly
# apostrophe) does not.
_PUNCTUATION_NORMALIZE = {
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "—": "-", "–": "-",
}
_PUNCTUATION_NORMALIZE_RE = re.compile("|".join(re.escape(c) for c in _PUNCTUATION_NORMALIZE))


def _strip_greeting_lines(body: str) -> str:
    # Substituting "." (not "") preserves a sentence-boundary marker through the
    # whitespace collapse that happens later in combine_subject_body/normalize_text —
    # without it, deleting a greeting line merges the sentence before it and the
    # sentence after it into one run with nothing but a space between them, and
    # fields.py's capitalized-word-run company capture can't tell where the greeting
    # used to be, so it can run straight into the next sentence's first (also
    # capitalized) word (e.g. "at Solace Systems" + "Unfortunately, we..." becomes
    # "Solace Systems Unfortunately"). See fields.py's _COMPANY_BOUNDARY, widened
    # alongside this change to actually stop there.
    stripped = _GREETING_LINE_RE.sub(".", body)
    if not stripped.strip(" .\n\t"):
        # The "greeting" match consumed the entire body (a short single-line message
        # that happens to open with a greeting word and end in a comma/colon, e.g.
        # "Hi Jordan, your offer is attached,") — stripping it would destroy the whole
        # message. Treat it as real content instead of a greeting in that case.
        return body
    return stripped


def _truncate_at_signature(body: str) -> str:
    match = _SIGNATURE_START_RE.search(body)
    if match is None:
        return body
    preceding = body[: match.start()].strip()
    if not preceding:
        # A sign-off word/phrase as the very first line (e.g. a body that opens with
        # "Congratulations,") is not a footer — a real signature/footer always comes
        # after actual message content. Without this guard, a message like that would
        # be truncated to nothing.
        return body
    return body[: match.start()]


def _normalize_punctuation(body: str) -> str:
    return _PUNCTUATION_NORMALIZE_RE.sub(lambda m: _PUNCTUATION_NORMALIZE[m.group(0)], body)


def preprocess_body(body: str) -> str:
    """Applied to the message body before any scoring or extraction. Order matters:
    greeting lines are stripped first (a greeting can itself contain a word the
    signature truncator would misfire on, e.g. "Hi Best,"), then the signature/footer
    is truncated, then punctuation is normalized last so it doesn't interfere with
    either line-anchored regex above."""
    body = _strip_greeting_lines(body)
    body = _truncate_at_signature(body)
    body = _normalize_punctuation(body)
    return body
