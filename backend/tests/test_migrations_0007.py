from sqlalchemy import inspect


def test_migration_0007_adds_the_phase_10_columns(engine) -> None:
    # Phase 10 CP2a ships this migration ALONE (no model reads these columns
    # yet), so this test — against the real migrated schema — is its only
    # coverage until CP2b.
    inspector = inspect(engine)
    gmail_cols = {c["name"]: c for c in inspector.get_columns("gmail_connections")}
    job_cols = {c["name"]: c for c in inspector.get_columns("sync_jobs")}
    assert gmail_cols["reauth_required_at"]["nullable"] is True
    assert job_cols["error_code"]["nullable"] is True
    assert job_cols["alerted_at"]["nullable"] is True
