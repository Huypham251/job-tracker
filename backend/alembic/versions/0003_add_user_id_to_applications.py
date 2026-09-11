"""add user_id to applications

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Local dev/test data only — no production users exist to preserve.
    op.execute("TRUNCATE TABLE applications")

    op.add_column(
        "applications",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.alter_column("applications", "user_id", nullable=False)
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_foreign_key(
        "fk_applications_user_id_users",
        "applications",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_applications_user_id_users", "applications", type_="foreignkey")
    op.drop_index("ix_applications_user_id", table_name="applications")
    op.drop_column("applications", "user_id")
