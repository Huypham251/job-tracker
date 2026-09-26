"""Private-data rules for the real-mail evaluation set (Phase 11 spec §3.1, §5).

Real email content never enters the repository. It lives in private_dir(), outside
the repo, and is scrubbed before it is written even there (defense in depth — the
directory is private, but a scrubbed copy is what we'd want if it ever leaked)."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PRIVATE_DIR = Path.home() / ".job-tracker-eval"

# Stands in for the maintainer everywhere real-derived text is stored or evaluated.
# Chosen because neither word appears in evaluation/dataset.jsonl's synthetic examples.
FICTIONAL_NAME = "Quinlan Ellery"
FICTIONAL_GIVEN = "Quinlan"
FICTIONAL_FAMILY = "Ellery"
FICTIONAL_EMAIL = "quinlan.ellery@example.com"
SCRUBBED_URL = "https://example.invalid/"


def ensure_outside_repo(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise SystemExit(
            f"Refusing to use {resolved}: private evaluation data must live outside the "
            f"repository ({REPO_ROOT})."
        )
    return resolved


def private_dir() -> Path:
    path = ensure_outside_repo(Path(os.environ.get("JOBTRACKER_REAL_EVAL_DIR", str(DEFAULT_PRIVATE_DIR))))
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Identity:
    name: str
    email: str
    extra_terms: tuple[str, ...] = ()


_EMAIL_RE = re.compile(r"[\w.+-]+@((?:[\w-]+\.)+[\w-]+)")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<![\w+])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\w)")
# Requisition/candidate IDs: an optional short letter prefix, then 6+ digits (hyphens
# allowed inside). Digits become 0 so the SHAPE survives — position extraction has to
# learn to strip these. A year ("2027") is too short to match.
_LONG_ID_RE = re.compile(r"\b([A-Za-z]{0,4})(\d[\d-]{4,}\d)\b")


def _whole_word(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)


def _name_replacements(name: str) -> list[tuple[re.Pattern[str], str]]:
    tokens = [t for t in name.split() if len(t) >= 2]
    if not tokens:
        return []
    if len(tokens) == 1:
        variants = [(tokens[0], FICTIONAL_GIVEN)]
    else:
        given, family = " ".join(tokens[:-1]), tokens[-1]
        variants = [
            (" ".join(tokens), FICTIONAL_NAME),
            (f"{family} {given}", FICTIONAL_NAME),
            (given, FICTIONAL_GIVEN),
            (family, FICTIONAL_FAMILY),
            *((token, FICTIONAL_GIVEN) for token in tokens[:-1]),
        ]
    # Longest first, so the full name is replaced before its parts are.
    variants.sort(key=lambda pair: len(pair[0]), reverse=True)
    return [(_whole_word(source), replacement) for source, replacement in variants]


def scrub(text: str, identity: Identity) -> str:
    if not text:
        return text
    text = _URL_RE.sub(SCRUBBED_URL, text)
    own_email = identity.email.strip().lower()

    def _email(match: re.Match[str]) -> str:
        return FICTIONAL_EMAIL if match.group(0).lower() == own_email else f"person@{match.group(1)}"

    text = _EMAIL_RE.sub(_email, text)
    for pattern, replacement in _name_replacements(identity.name):
        text = pattern.sub(replacement, text)
    for term in identity.extra_terms:
        if term.strip():
            text = _whole_word(term.strip()).sub("[redacted]", text)
    text = _PHONE_RE.sub("000-000-0000", text)
    return _LONG_ID_RE.sub(lambda m: m.group(1) + re.sub(r"\d", "0", m.group(2)), text)
