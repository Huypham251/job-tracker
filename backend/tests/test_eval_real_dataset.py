import json

import pytest

from evaluation.real import dataset


@pytest.fixture(autouse=True)
def _private(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_REAL_EVAL_DIR", str(tmp_path / "eval"))


def _raw(ref: str) -> dict:
    return {
        "ref": ref, "origin": "pending_review", "selected_by": "reviewed",
        "subject": f"subject {ref}", "sender": "Kestrel <person@kestrel.com>",
        "date": "Mon, 5 Jan 2026 10:00:00 +0000", "mime_type": "text/plain",
        "raw_body": "Thank you for applying.", "stored": {}, "review": None,
    }


def _related(ref: str, status: str = "applied") -> dict:
    return {"ref": ref, "is_job_related": True, "status": status, "company": "Kestrel", "position": None}


def _unrelated(ref: str) -> dict:
    return {"ref": ref, "is_job_related": False, "status": None, "company": None, "position": None}


@pytest.mark.parametrize(
    "label",
    [
        {"is_job_related": True, "status": "applied"},  # no ref
        {"ref": "r", "is_job_related": "yes"},
        {"ref": "r", "is_job_related": True, "status": "hired"},
        {"ref": "r", "is_job_related": False, "status": "applied", "company": None, "position": None},
    ],
)
def test_validate_label_rejects_malformed_labels(label) -> None:
    with pytest.raises(ValueError):
        dataset.validate_label(label)


def test_raw_round_trip_and_labels_last_line_wins() -> None:
    dataset.write_raw(_raw("msg-a"))
    assert dataset.load_raw("msg-a")["subject"] == "subject msg-a"
    dataset.append_label(_related("msg-a"))
    dataset.append_label({**_related("msg-a"), "company": "Kestrel Labs"})
    assert dataset.load_labels()["msg-a"]["company"] == "Kestrel Labs"


def test_stratified_split_is_deterministic_and_keeps_each_bucket_on_both_sides() -> None:
    labels = {f"msg-{i}": _related(f"msg-{i}") for i in range(10)}
    labels |= {f"neg-{i}": _unrelated(f"neg-{i}") for i in range(4)}
    split = dataset.stratified_split(labels)
    assert split == dataset.stratified_split(labels)
    applied = [split[f"msg-{i}"] for i in range(10)]
    assert applied.count("dev") == 7 and applied.count("test") == 3
    negatives = [split[f"neg-{i}"] for i in range(4)]
    assert "dev" in negatives and "test" in negatives


def test_freeze_split_never_moves_an_already_frozen_example() -> None:
    for i in range(5):
        dataset.write_raw(_raw(f"msg-{i}"))
        dataset.append_label(_related(f"msg-{i}"))
    first = dataset.freeze_split()
    for i in range(5, 12):
        dataset.write_raw(_raw(f"msg-{i}"))
        dataset.append_label(_related(f"msg-{i}"))
    second = dataset.freeze_split()
    assert all(second[ref] == side for ref, side in first.items())
    assert len(second) == 12


def test_load_real_examples_builds_evaluation_examples_and_skips_skipped() -> None:
    dataset.write_raw(_raw("msg-a"))
    dataset.write_raw(_raw("msg-b"))
    dataset.append_label(_related("msg-a"))
    dataset.append_label({"ref": "msg-b", "skip": True})
    dataset.freeze_split()
    examples = dataset.load_real_examples("all")
    assert [e["ref"] for e in examples] == ["msg-a"]
    example = examples[0]
    assert example["raw_body"] == "Thank you for applying." and example["mime_type"] == "text/plain"
    assert example["expected"] == {"is_job_related": True, "status": "applied", "company": "Kestrel", "position": None}
    assert example["category"] == "real_pending_review"


def test_load_real_examples_requires_a_frozen_split() -> None:
    with pytest.raises(SystemExit):
        dataset.load_real_examples("dev")
