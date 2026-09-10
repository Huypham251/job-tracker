import enum
import uuid
from datetime import date

from sqlalchemy import Date, Enum as SAEnum, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ApplicationStatus(enum.StrEnum):
    applied = "applied"
    oa = "oa"
    interview = "interview"
    rejected = "rejected"
    offer = "offer"
    withdrawn = "withdrawn"


class Application(TimestampMixin, Base):
    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ApplicationStatus] = mapped_column(
        SAEnum(
            ApplicationStatus,
            name="application_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ApplicationStatus.applied,
    )
    applied_at: Mapped[date | None] = mapped_column(Date, nullable=True)
