from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProcessResult(BaseModel):
    processed: int
    auto_applied: int
    queued_for_review: int
    ignored: int


class ReviewItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject: str
    sender: str
    snippet: str
    confidence: float
    proposed_action: str | None
    matched_application_id: UUID | None
    extracted_company: str | None
    extracted_position: str | None
    extracted_status: str | None
    extracted_status_date: date | None
    created_at: datetime


class ReviewDecision(BaseModel):
    company: str | None = None
    position: str | None = None
    status: str | None = None
    status_date: date | None = None
