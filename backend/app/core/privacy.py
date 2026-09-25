import hashlib


def message_ref(message_id: str) -> str:
    """A stable, non-reversible stand-in for a Gmail message ID in log lines.
    Production worker logs are public (GitHub Actions on a public repo), so
    raw IDs never go there; hash a row's gmail_message_id the same way to
    match a log line back to it."""
    return "msg-" + hashlib.sha256(message_id.encode()).hexdigest()[:10]
