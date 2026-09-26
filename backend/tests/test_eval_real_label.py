import pytest

from app.classifier.schemas import EmailExtraction
from evaluation.real import dataset
from evaluation.real.label import prefill, run


@pytest.fixture(autouse=True)
def _private(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_REAL_EVAL_DIR", str(tmp_path / "eval"))


class _Fixed:
    def __init__(self, result: EmailExtraction) -> None:
        self.result = result

    def classify_and_extract(self, **kwargs) -> EmailExtraction:
        return self.result


GUESS = _Fixed(EmailExtraction(is_job_related=True, confidence=0.5, status="applied", company="Kestrel"))


def _raw(ref: str, review=None, origin="pending_review") -> dict:
    return {"ref": ref, "origin": origin, "selected_by": "reviewed", "subject": "Thanks for applying",
            "sender": "person@kestrel.com", "date": "d", "mime_type": "text/plain",
            "raw_body": "Thank you for applying to Kestrel.", "stored": {}, "review": review}


def _script(*answers: str):
    answers_iter = iter(answers)
    return lambda _prompt="": next(answers_iter)


def test_prefill_prefers_the_maintainers_own_review_decision() -> None:
    raw = _raw("msg-a", review={"company": "Kestrel Labs", "position": "Analyst", "status": "interview"}, origin="approved")
    assert prefill(raw, GUESS) == {"is_job_related": True, "status": "interview", "company": "Kestrel Labs", "position": "Analyst"}


def test_accept_not_related_skip_and_quit() -> None:
    for ref in ("msg-a", "msg-b", "msg-c", "msg-d"):
        dataset.write_raw(_raw(ref))
    written = run(prompt=_script("a", "n", "s", "q"), out=lambda *_: None, extractor=GUESS)
    labels = dataset.load_labels()
    assert written == 3
    assert labels["msg-a"] == {"ref": "msg-a", "is_job_related": True, "status": "applied", "company": "Kestrel", "position": None}
    assert labels["msg-b"]["is_job_related"] is False
    assert labels["msg-c"] == {"ref": "msg-c", "skip": True}
    assert "msg-d" not in labels


def test_edit_keeps_defaults_on_enter_and_dash_clears_a_field() -> None:
    dataset.write_raw(_raw("msg-a"))
    # e(dit): job-related [y] ⏎, status: "bogus" (rejected) then "oa", company ⏎ (keep), position "-" (none)
    run(prompt=_script("e", "", "bogus", "oa", "", "-"), out=lambda *_: None, extractor=GUESS)
    assert dataset.load_labels()["msg-a"] == {"ref": "msg-a", "is_job_related": True, "status": "oa", "company": "Kestrel", "position": None}


def test_is_resumable() -> None:
    dataset.write_raw(_raw("msg-a"))
    dataset.write_raw(_raw("msg-b"))
    run(prompt=_script("a", "q"), out=lambda *_: None, extractor=GUESS)
    assert run(prompt=_script("a"), out=lambda *_: None, extractor=GUESS) == 1
    assert set(dataset.load_labels()) == {"msg-a", "msg-b"}
