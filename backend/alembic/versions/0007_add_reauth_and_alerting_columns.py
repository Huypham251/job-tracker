"""add gmail reauth flag, sync job error code and alert bookkeeping

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-25

Deployed ALONE, before any code that reads these columns (Phase 10 spec §7):
the GitHub Actions worker runs main as soon as it's pushed, while Render only
migrates during its build. All three columns are nullable, so code from before
this migration keeps working against the new schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gmail_connections", sa.Column("reauth_required_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("sync_jobs", sa.Column("error_code", sa.String(length=40), nullable=True))
    op.add_column("sync_jobs", sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True))
    # Failures from before monitoring existed are history, not news — the
    # monitor's first run must not alert on them.
    op.execute("UPDATE sync_jobs SET alerted_at = now() WHERE status = 'failed'")


def downgrade() -> None:
    op.drop_column("sync_jobs", "alerted_at")
    op.drop_column("sync_jobs", "error_code")
    op.drop_column("gmail_connections", "reauth_required_at")
