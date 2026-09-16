"""add pipeline tables and application source/other-status

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres allows ALTER TYPE ... ADD VALUE inside a transaction (PG12+),
    # but the NEW value cannot be used in that SAME transaction. Alembic runs
    # each migration in one transaction by default, so a future migration
    # that both adds a new enum value AND references it (an UPDATE, a CHECK
    # constraint, a backfill) in the same file will fail on a fresh database
    # where migrations run back-to-back. Split such a change across two
    # migrations if that need ever arises.
    op.execute("ALTER TYPE application_status ADD VALUE IF NOT EXISTS 'other'")

    op.add_column(
        "applications",
        sa.Column("source", sa.String(length=10), nullable=False, server_default="manual"),
    )

    op.create_table(
        "processed_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=998), nullable=False),
        sa.Column("sender", sa.String(length=998), nullable=False),
        sa.Column("message_date", sa.String(length=255), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("is_job_related", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("extracted_company", sa.String(length=255), nullable=True),
        sa.Column("extracted_position", sa.String(length=255), nullable=True),
        sa.Column("extracted_status", sa.String(length=50), nullable=True),
        sa.Column("extracted_status_date", sa.Date(), nullable=True),
        sa.Column("matched_application_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("proposed_action", sa.String(length=10), nullable=True),
        sa.Column("review_status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.create_index("ix_processed_messages_user_id", "processed_messages", ["user_id"])
    op.create_unique_constraint(
        "uq_processed_messages_user_id_gmail_message_id",
        "processed_messages",
        ["user_id", "gmail_message_id"],
    )
    op.create_foreign_key(
        "fk_processed_messages_user_id_users",
        "processed_messages",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_processed_messages_matched_application_id_applications",
        "processed_messages",
        "applications",
        ["matched_application_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_table("processed_messages")
    op.drop_column("applications", "source")
    # Postgres does not support removing a value from an enum type — the 'other'
    # value added by upgrade() is intentionally left in place.
