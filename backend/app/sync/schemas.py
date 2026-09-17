from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SyncJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_type: str
    status: str
    attempts: int
    window_start: date
    messages_seen: int
    messages_processed: int
    auto_applied: int
    queued_for_review: int
    ignored: int
    failed_count: int
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
