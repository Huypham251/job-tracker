"""add sync_jobs table, gmail sync watermark, processed_messages audit link

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("page_token", sa.String(length=255), nullable=True),
        sa.Column("messages_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("auto_applied", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queued_for_review", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ignored", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_sync_jobs_user_id", "sync_jobs", ["user_id"])
    op.create_index(
        "uq_sync_jobs_user_active",
        "sync_jobs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_foreign_key(
        "fk_sync_jobs_user_id_users", "sync_jobs", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )

    op.add_column(
        "gmail_connections", sa.Column("last_synced_message_date", sa.Date(), nullable=True)
    )

    op.add_column(
        "processed_messages",
        sa.Column("sync_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_processed_messages_sync_job_id_sync_jobs",
        "processed_messages",
        "sync_jobs",
        ["sync_job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_processed_messages_sync_job_id_sync_jobs", "processed_messages", type_="foreignkey"
    )
    op.drop_column("processed_messages", "sync_job_id")
    op.drop_column("gmail_connections", "last_synced_message_date")
    op.drop_table("sync_jobs")
