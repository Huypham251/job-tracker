"""Label the exported real messages (Phase 11 spec §3.1). Resumable — quit any time.

    cd backend
    uv run python -m evaluation.real.label
"""

from app.classifier.extractor import RuleBasedExtractor
from app.gmail.google_api import clean_body
from evaluation.real.dataset import STATUSES, append_label, iter_raw, load_labels

GUIDELINES = """\
Job-related = about YOUR application/candidacy with a specific employer: confirmation
  (including job-board "application sent to <Co>"), assessment invite or receipt,
  interview, rejection, offer, status update, reminder about an assessment for an
  application you submitted.
Not job-related: job alerts/recommendations, "complete/start your application"
  reminders for applications never submitted, account mail (passwords, login codes),
  community posts, newsletters, marketing, admissions.
company  = the employer as the email names it.
position = the title as stated; '-' if the email doesn't state one.
status   = applied | oa | interview | rejected | offer | other (withdrawn-type mail = other).
Keys: a=accept  n=not job-related  e=edit  s=skip  q=quit  ?=these guidelines"""

_NOT_RELATED = {"is_job_related": False, "status": None, "company": None, "position": None}


def prefill(raw: dict, extractor) -> dict:
    review = raw.get("review")
    if review:
        return {"is_job_related": True, "status": review.get("status"),
                "company": review.get("company"), "position": review.get("position")}
    result = extractor.classify_and_extract(
        subject=raw["subject"], sender=raw["sender"], date=raw["date"],
        body=clean_body(raw["raw_body"], raw["mime_type"]),
    )
    if not result.is_job_related:
        return dict(_NOT_RELATED)
    return {"is_job_related": True, "status": result.status, "company": result.company, "position": result.position}


def _show(raw: dict, suggestion: dict, out) -> None:
    body = clean_body(raw["raw_body"], raw["mime_type"])
    out("\n" + "=" * 78)
    out(f"[{raw['origin']} / {raw['selected_by']}] {raw['subject']}")
    out(f"from: {raw['sender']}   date: {raw['date']}")
    out("-" * 78)
    out(body[:1500] + (" …" if len(body) > 1500 else ""))
    out("-" * 78)
    if raw["origin"] == "rejected":
        out("(you rejected this in the review queue — not job-related, or just a bad extraction?)")
    out(f"suggested: {suggestion}")


def _ask(prompt, out, field: str, default, choices=None):
    while True:
        answer = prompt(f"  {field} [{'-' if default is None else default}]: ").strip()
        value = default if answer == "" else (None if answer == "-" else answer)
        if choices is None or value in choices:
            return value
        out(f"  must be one of: {', '.join(choices)}")


def _edit(suggestion: dict, prompt, out) -> dict:
    related = _ask(prompt, out, "job-related (y/n)", "y" if suggestion["is_job_related"] else "n", choices=("y", "n"))
    if related == "n":
        return dict(_NOT_RELATED)
    return {
        "is_job_related": True,
        "status": _ask(prompt, out, "status", suggestion["status"] or "applied", choices=STATUSES),
        "company": _ask(prompt, out, "company", suggestion["company"]),
        "position": _ask(prompt, out, "position ('-' = not stated)", suggestion["position"]),
    }


def run(*, prompt=input, out=print, extractor=None) -> int:
    extractor = extractor or RuleBasedExtractor()
    labeled = load_labels()
    pending = [raw for raw in iter_raw() if raw["ref"] not in labeled]
    out(f"{len(pending)} to label, {len(labeled)} already labeled.  ? = guidelines")
    written = 0
    for raw in pending:
        suggestion = prefill(raw, extractor)
        _show(raw, suggestion, out)
        while True:
            key = prompt("> ").strip().lower()
            if key == "q":
                return written
            if key == "?":
                out(GUIDELINES)
                continue
            if key == "a":
                label = {"ref": raw["ref"], **suggestion}
            elif key == "n":
                label = {"ref": raw["ref"], **_NOT_RELATED}
            elif key == "s":
                label = {"ref": raw["ref"], "skip": True}
            elif key == "e":
                label = {"ref": raw["ref"], **_edit(suggestion, prompt, out)}
            else:
                out("a / n / e / s / q / ?")
                continue
            try:
                append_label(label)
            except ValueError as exc:
                out(f"Not saved: {exc} — press e to edit.")
                continue
            written += 1
            break
    return written


def main() -> None:
    print(f"Labeled {run()} message(s).")


if __name__ == "__main__":
    main()
