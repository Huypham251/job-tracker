import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    pass


class ProcessedMessage(TimestampMixin, Base):
    __tablename__ = "processed_messages"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "gmail_message_id", name="uq_processed_messages_user_id_gmail_message_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gmail_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    sender: Mapped[str] = mapped_column(String(998), nullable=False)
    message_date: Mapped[str] = mapped_column(String(255), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    is_job_related: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    extracted_company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extracted_position: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extracted_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    extracted_status_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    matched_application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="SET NULL"),
        nullable=True,
    )
    proposed_action: Mapped[str | None] = mapped_column(String(10), nullable=True)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False)
