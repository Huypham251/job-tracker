"""Export real messages for the private evaluation set (Phase 11 spec §3.1).

LOCAL ONLY: reads the local dev database and the locally connected Gmail account.
First let a local sync catch up with the mailbox (see README "Real-mail evaluation"):

    cd backend
    uv run python -m evaluation.real.export --user-email you@example.com [--extra-term "12 Elm Row"]

Writes one scrubbed JSON file per message into <private dir>/raw/ (re-runs skip files
that already exist) and <private dir>/identity.json (used by check_leaks). Prints
counts only."""

import argparse
import json
import random
import re
from collections import Counter
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.models import Application
from app.core.privacy import message_ref
from app.gmail.google_api import GmailAuthError, GoogleApiError
from app.pipeline.models import ProcessedMessage
from evaluation.real.dataset import raw_path, write_raw
from evaluation.real.privacy import Identity, private_dir, scrub

JOB_KEYWORDS = re.compile(
    r"appl(?:y|ied|ying|ication)|interview|assessment|offer|candida|next steps|"
    r"hackerrank|codesignal|recruit|hiring|position|thank you for your interest",
    re.IGNORECASE,
)
REVIEWED_STATES = ("pending_review", "approved", "rejected", "auto_applied")
DEFAULT_IGNORED_SAMPLE = 150
DEFAULT_SEED = 11

FetchRaw = Callable[[str], tuple[dict, str | None, str]]


def select_candidates(
    db: Session, user_id: UUID, *, ignored_sample: int, seed: int
) -> list[tuple[ProcessedMessage, str]]:
    reviewed = list(db.scalars(
        select(ProcessedMessage)
        .where(ProcessedMessage.user_id == user_id, ProcessedMessage.review_status.in_(REVIEWED_STATES))
        .order_by(ProcessedMessage.created_at, ProcessedMessage.gmail_message_id)
    ))
    ignored = list(db.scalars(
        select(ProcessedMessage)
        .where(ProcessedMessage.user_id == user_id, ProcessedMessage.review_status == "ignored")
        .order_by(ProcessedMessage.gmail_message_id)
    ))
    keyword_hits = [row for row in ignored if JOB_KEYWORDS.search(row.subject)]
    others = [row for row in ignored if not JOB_KEYWORDS.search(row.subject)]
    sample = random.Random(seed).sample(others, min(ignored_sample, len(others)))
    return (
        [(row, "reviewed") for row in reviewed]
        + [(row, "keyword") for row in keyword_hits]
        + [(row, "sample") for row in sample]
    )


def _review_values(db: Session, row: ProcessedMessage) -> dict | None:
    if row.review_status != "approved" or row.matched_application_id is None:
        return None
    application = db.get(Application, row.matched_application_id)
    if application is None:
        return None
    return {"company": application.company or None, "position": application.position or None,
            "status": application.status.value}


def _scrub_values(values: dict | None, identity: Identity) -> dict | None:
    if values is None:
        return None
    return {key: scrub(value, identity) if isinstance(value, str) else value for key, value in values.items()}


def export_candidates(
    db: Session, user_id: UUID, fetch: FetchRaw, identity: Identity, *, ignored_sample: int, seed: int
) -> Counter:
    counts: Counter = Counter()
    for row, selected_by in select_candidates(db, user_id, ignored_sample=ignored_sample, seed=seed):
        ref = message_ref(row.gmail_message_id)
        if raw_path(ref).exists():
            counts["already_exported"] += 1
            continue
        try:
            summary, mime_type, raw = fetch(row.gmail_message_id)
        except GmailAuthError:
            raise SystemExit("Gmail access needs reconnecting in the local app before exporting.")
        except GoogleApiError:
            counts["fetch_failed"] += 1
            continue
        write_raw({
            "ref": ref,
            "origin": row.review_status,
            "selected_by": selected_by,
            "subject": scrub(summary["subject"], identity),
            "sender": scrub(summary["from_"], identity),
            "date": summary["date"],
            "mime_type": mime_type,
            "raw_body": scrub(raw, identity),
            "stored": _scrub_values({
                "is_job_related": row.is_job_related, "confidence": row.confidence,
                "company": row.extracted_company, "position": row.extracted_position,
                "status": row.extracted_status,
            }, identity),
            "review": _scrub_values(_review_values(db, row), identity),
        })
        counts["exported"] += 1
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export real messages for the private evaluation set.")
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--extra-term", action="append", default=[],
                        help="another personal string to redact (phone, street, school); repeatable")
    parser.add_argument("--ignored-sample", type=int, default=DEFAULT_IGNORED_SAMPLE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    from app.db.session import SessionLocal
    from app.gmail import google_api
    from app.gmail import service as gmail_service
    from app.users.models import User

    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.email == args.user_email)).one_or_none()
        if user is None:
            raise SystemExit("No local user with that email.")
        connection = gmail_service.get_connection(db, user.id)
        if connection is None:
            raise SystemExit("That user has no local Gmail connection — connect Gmail in the local app first.")
        extra = list(args.extra_term)
        if connection.google_email.lower() != user.email.lower():
            extra.append(connection.google_email)
        identity = Identity(name=user.name, email=user.email, extra_terms=tuple(extra))
        (private_dir() / "identity.json").write_text(json.dumps(
            {"name": identity.name, "email": identity.email, "extra_terms": list(identity.extra_terms)}
        ))

        def fetch(message_id: str):
            return gmail_service.call_with_fresh_token(
                db, connection, lambda token: google_api.get_message_raw(token, message_id)
            )

        counts = export_candidates(db, user.id, fetch, identity, ignored_sample=args.ignored_sample, seed=args.seed)
    for key, value in sorted(counts.items()):
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
