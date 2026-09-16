import re
from dataclasses import dataclass
from uuid import UUID

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.models import Application

STRONG_MATCH_THRESHOLD = 85
NO_MATCH_THRESHOLD = 60
AMBIGUOUS_MARGIN = 10

_SUFFIX_PATTERN = re.compile(r"\b(inc|llc|corp|corporation|ltd|co)\.?\b", re.IGNORECASE)
_PUNCTUATION_PATTERN = re.compile(r"[^\w\s]")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = _SUFFIX_PATTERN.sub("", text)
    text = _PUNCTUATION_PATTERN.sub("", text)
    text = _WHITESPACE_PATTERN.sub(" ", text)
    return text.strip().lower()


@dataclass
class MatchResult:
    application: "Application | None"
    action: str
    ambiguous: bool


def find_candidate(db: Session, user_id: UUID, company: str, position: str) -> MatchResult:
    target = normalize(f"{company} {position}")
    candidates = list(db.scalars(select(Application).where(Application.user_id == user_id)))

    scored = sorted(
        (
            (fuzz.ratio(target, normalize(f"{app.company} {app.position}")), app)
            for app in candidates
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )

    if not scored or scored[0][0] < NO_MATCH_THRESHOLD:
        return MatchResult(application=None, action="create", ambiguous=False)

    top_score, top_app = scored[0]
    runner_up_score = scored[1][0] if len(scored) > 1 else 0

    if top_score >= STRONG_MATCH_THRESHOLD and (top_score - runner_up_score) >= AMBIGUOUS_MARGIN:
        return MatchResult(application=top_app, action="update", ambiguous=False)

    return MatchResult(application=top_app, action="update", ambiguous=True)
