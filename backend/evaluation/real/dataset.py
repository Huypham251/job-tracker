"""Storage for the private real-mail evaluation set (Phase 11 spec §3.1): one scrubbed
JSON file per exported message, a labels file, and a frozen dev/test split — all in
private_dir(), never in the repo.

    cd backend
    uv run python -m evaluation.real.dataset --stats
    uv run python -m evaluation.real.dataset --freeze-split     # after labeling
    uv run python -m evaluation.real.dataset --list-dev         # refs to pick for pseudonymizing
"""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from evaluation.real.privacy import private_dir

STATUSES = ("applied", "oa", "interview", "rejected", "offer", "other")
RAW_DIR = "raw"
LABELS_FILE = "labels.jsonl"
SPLIT_FILE = "split.json"
DEV_FRACTION = 0.7


def _raw_dir() -> Path:
    path = private_dir() / RAW_DIR
    path.mkdir(exist_ok=True)
    return path


def raw_path(ref: str) -> Path:
    return _raw_dir() / f"{ref}.json"


def write_raw(record: dict) -> None:
    raw_path(record["ref"]).write_text(json.dumps(record, ensure_ascii=False, indent=1))


def load_raw(ref: str) -> dict:
    return json.loads(raw_path(ref).read_text())


def iter_raw() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(_raw_dir().glob("*.json"))]


def validate_label(label: dict) -> None:
    if not isinstance(label.get("ref"), str) or not label["ref"]:
        raise ValueError("a label needs a ref")
    if label.get("skip") is True:
        return
    if not isinstance(label.get("is_job_related"), bool):
        raise ValueError("is_job_related must be true or false")
    if label["is_job_related"]:
        if label.get("status") not in STATUSES:
            raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    elif any(label.get(key) is not None for key in ("status", "company", "position")):
        raise ValueError("a not-job-related label has no status, company or position")


def append_label(label: dict) -> None:
    validate_label(label)
    with (private_dir() / LABELS_FILE).open("a") as f:
        f.write(json.dumps(label, ensure_ascii=False) + "\n")


def load_labels() -> dict[str, dict]:
    path = private_dir() / LABELS_FILE
    if not path.exists():
        return {}
    labels: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            label = json.loads(line)
            labels[label["ref"]] = label  # a later line (a correction) wins
    return labels


def _hash_key(ref: str) -> str:
    return hashlib.sha256(f"phase11-split:{ref}".encode()).hexdigest()


def stratified_split(labels: dict[str, dict]) -> dict[str, str]:
    """70/30 within each (is_job_related, status) bucket, ordered by a hash of the ref —
    deterministic, and every bucket with 2+ examples lands on both sides."""
    buckets: dict[tuple, list[str]] = defaultdict(list)
    for ref, label in labels.items():
        if not label.get("skip"):
            buckets[(label["is_job_related"], label.get("status"))].append(ref)
    split: dict[str, str] = {}
    for refs in buckets.values():
        refs.sort(key=_hash_key)
        n_dev = round(len(refs) * DEV_FRACTION)
        if len(refs) >= 2:
            n_dev = min(max(n_dev, 1), len(refs) - 1)
        for i, ref in enumerate(refs):
            split[ref] = "dev" if i < n_dev else "test"
    return split


def load_frozen_split() -> dict[str, str]:
    path = private_dir() / SPLIT_FILE
    if not path.exists():
        raise SystemExit("No frozen split yet — run `python -m evaluation.real.dataset --freeze-split` after labeling.")
    return json.loads(path.read_text())


def freeze_split() -> dict[str, str]:
    """Adds newly labeled refs to the frozen split; never moves an existing one (so a
    test example can't leak into dev because more labels arrived)."""
    path = private_dir() / SPLIT_FILE
    frozen = json.loads(path.read_text()) if path.exists() else {}
    new = {ref: label for ref, label in load_labels().items() if ref not in frozen}
    frozen.update(stratified_split(new))
    path.write_text(json.dumps(frozen, indent=1, sort_keys=True))
    return frozen


def to_example(raw: dict, label: dict) -> dict:
    expected: dict = {"is_job_related": label["is_job_related"]}
    if label["is_job_related"]:
        expected.update(status=label["status"], company=label.get("company"), position=label.get("position"))
    return {
        "ref": raw["ref"],
        "category": f"real_{raw['origin']}",
        "subject": raw["subject"],
        "sender": raw["sender"],
        "date": raw["date"],
        "raw_body": raw["raw_body"],
        "mime_type": raw["mime_type"],
        "expected": expected,
    }


def load_real_examples(split: str) -> list[dict]:
    frozen = load_frozen_split()
    examples = []
    for ref, label in sorted(load_labels().items()):
        if label.get("skip") or ref not in frozen:
            continue
        if split != "all" and frozen[ref] != split:
            continue
        examples.append(to_example(load_raw(ref), label))
    return examples


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Private real-mail evaluation set.")
    parser.add_argument("--freeze-split", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--list-dev", action="store_true", help="dev refs with label bucket and subject (local only)")
    args = parser.parse_args(argv)

    if args.freeze_split:
        frozen = freeze_split()
        print(f"Frozen split: {Counter(frozen.values())}")
    if args.stats:
        labels = load_labels()
        raws = iter_raw()
        print(f"exported={len(raws)} labeled={sum(1 for l in labels.values() if not l.get('skip'))} "
              f"skipped={sum(1 for l in labels.values() if l.get('skip'))} unlabeled={len(raws) - len(labels)}")
        buckets = Counter((l["is_job_related"], l.get("status")) for l in labels.values() if not l.get("skip"))
        for bucket, count in sorted(buckets.items(), key=str):
            print(f"  {bucket}: {count}")
    if args.list_dev:
        frozen = load_frozen_split()
        labels = load_labels()
        for ref, side in sorted(frozen.items()):
            if side == "dev":
                label = labels[ref]
                print(f"{ref}  related={label['is_job_related']} status={label.get('status')}  {load_raw(ref)['subject']}")


if __name__ == "__main__":
    main()
